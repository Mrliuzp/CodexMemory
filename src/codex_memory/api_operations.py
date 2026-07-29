"""V1.5 OpenAPI operation 的解析、归一化与确定性文档生成。"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Iterable

import yaml
from openapi_spec_validator import validate_spec


MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_OPERATIONS = 500
MAX_DEPTH = 64
METHOD_ORDER = ("get", "post", "put", "patch", "delete", "options", "head", "trace")
METHOD_RANK = {method: index for index, method in enumerate(METHOD_ORDER)}


class OpenAPIContractError(ValueError):
    """表示输入不能满足 V1.5 冻结契约。"""

    def __init__(self, message: str, errors: list[dict[str, Any]] | None = None) -> None:
        self.errors = errors or [{"code": "invalid_document", "message": message}]
        super().__init__(message)


@dataclass(frozen=True)
class ParsedOperation:
    method: str
    path: str
    operation_id: str
    summary: str | None
    tags: list[str]
    operation: dict[str, Any]
    operation_hash: str


@dataclass(frozen=True)
class NormalizedOpenAPI:
    source_document: dict[str, Any]
    source_version: str
    document: dict[str, Any]
    normalized_version: str
    content_hash: str
    operations: list[ParsedOperation]
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    markdown: str


def _issue(code: str, message: str, path: str | None = None, **extra: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"code": code, "message": message}
    if path is not None:
        value["path"] = path
    value.update(extra)
    return value


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=lambda item: str(item))}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON 常量不合法：{value}")


def _validate_json_values(value: Any, path: str, seen: set[int]) -> None:
    """拒绝无法稳定序列化的 YAML 专有标量和非有限浮点数。"""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise OpenAPIContractError("OpenAPI 文档不能包含非有限数字", [_issue("invalid_number", "OpenAPI 文档不能包含非有限数字", path)])
        return
    if isinstance(value, dict):
        identity = id(value)
        if identity in seen:
            raise OpenAPIContractError("OpenAPI 文档不能包含 YAML 别名循环", [_issue("document_cycle", "OpenAPI 文档不能包含 YAML 别名循环", path)])
        seen.add(identity)
        for key, item in value.items():
            _validate_json_values(item, f"{path}/{key}" if path else f"/{key}", seen)
        seen.remove(identity)
        return
    if isinstance(value, list):
        identity = id(value)
        if identity in seen:
            raise OpenAPIContractError("OpenAPI 文档不能包含 YAML 别名循环", [_issue("document_cycle", "OpenAPI 文档不能包含 YAML 别名循环", path)])
        seen.add(identity)
        for index, item in enumerate(value):
            _validate_json_values(item, f"{path}/{index}", seen)
        seen.remove(identity)
        return
    raise OpenAPIContractError("OpenAPI 文档包含不支持的 YAML 标量", [_issue("unsupported_scalar", "OpenAPI 文档包含不支持的 YAML 标量，请使用字符串", path)])


def _json_path(parts: Iterable[Any]) -> str:
    values = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(values) if values else "/"


def _validate_openapi_spec(document: dict[str, Any]) -> None:
    try:
        validate_spec(document)
    except Exception as error:
        path = _json_path(getattr(error, "path", ()))
        message = f"OpenAPI 规范校验失败（JSON 路径 {path}）：{error}"
        raise OpenAPIContractError(message, [_issue("openapi_validation_error", message, path)]) from error


def _reject_structural_features(document: dict[str, Any]) -> None:
    """只按 OpenAPI 对象位置拒绝 callbacks、webhooks 和 links。"""
    if any(key in document for key in ("callbacks", "webhooks", "links")):
        key = next(key for key in ("callbacks", "webhooks", "links") if key in document)
        raise OpenAPIContractError(f"不支持 OpenAPI 字段：{key}", [_issue("unsupported_feature", f"不支持 OpenAPI 字段：{key}", f"/{key}")])
    components = document.get("components")
    if isinstance(components, dict):
        for key in ("callbacks", "links", "webhooks"):
            if key in components:
                raise OpenAPIContractError(f"不支持 OpenAPI 字段：{key}", [_issue("unsupported_feature", f"不支持 OpenAPI 字段：{key}", f"/components/{key}")])

    def inspect_path_item(path: str, value: Any) -> None:
        path_item = _resolve(value, document)
        if not isinstance(path_item, dict):
            return
        for method in METHOD_RANK:
            if method not in path_item:
                continue
            operation = _resolve(path_item[method], document)
            if not isinstance(operation, dict):
                continue
            if "callbacks" in operation:
                raise OpenAPIContractError("不支持 OpenAPI 字段：callbacks", [_issue("unsupported_feature", "不支持 OpenAPI 字段：callbacks", f"/paths/{path}/{method}/callbacks")])
            responses = operation.get("responses")
            if isinstance(responses, dict):
                for response_name, response_value in responses.items():
                    response = _resolve(response_value, document)
                    if isinstance(response, dict) and "links" in response:
                        raise OpenAPIContractError("不支持 OpenAPI 字段：links", [_issue("unsupported_feature", "不支持 OpenAPI 字段：links", f"/paths/{path}/{method}/responses/{response_name}/links")])

    paths = document.get("paths")
    if isinstance(paths, dict):
        for path, path_item in paths.items():
            inspect_path_item(str(path), path_item)
    path_items = components.get("pathItems") if isinstance(components, dict) else None
    if isinstance(path_items, dict):
        for name, path_item in path_items.items():
            inspect_path_item(f"components/pathItems/{name}", path_item)
    responses = components.get("responses") if isinstance(components, dict) else None
    if isinstance(responses, dict):
        for name, response_value in responses.items():
            response = _resolve(response_value, document)
            if isinstance(response, dict) and "links" in response:
                raise OpenAPIContractError("不支持 OpenAPI 字段：links", [_issue("unsupported_feature", "不支持 OpenAPI 字段：links", f"/components/responses/{name}/links")])


def _decode_document(filename: str, content: bytes) -> dict[str, Any]:
    if not isinstance(filename, str) or not filename.lower().endswith((".json", ".yaml", ".yml")):
        raise OpenAPIContractError("文件扩展名必须是 .json、.yaml 或 .yml", [_issue("invalid_extension", "文件扩展名必须是 .json、.yaml 或 .yml")])
    if len(content) > MAX_DOCUMENT_BYTES:
        raise OpenAPIContractError("OpenAPI 文件不能超过 2 MiB", [_issue("document_too_large", "OpenAPI 文件不能超过 2 MiB")])
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise OpenAPIContractError("OpenAPI 文件必须是 UTF-8 编码", [_issue("invalid_encoding", "OpenAPI 文件必须是 UTF-8 编码")]) from error
    try:
        value = json.loads(text, parse_constant=_reject_json_constant) if filename.lower().endswith(".json") else yaml.safe_load(text)
    except (json.JSONDecodeError, ValueError, yaml.YAMLError) as error:
        raise OpenAPIContractError("OpenAPI 文件格式无效", [_issue("invalid_syntax", "OpenAPI 文件格式无效")]) from error
    if not isinstance(value, dict):
        raise OpenAPIContractError("OpenAPI 文档根节点必须是对象", [_issue("invalid_root", "OpenAPI 文档根节点必须是对象")])
    _validate_json_values(value, "", set())
    return value


def _walk(value: Any, path: str, depth: int, refs: list[tuple[str, str]], seen: set[int]) -> None:
    if depth > MAX_DEPTH:
        raise OpenAPIContractError("OpenAPI 文档结构深度不能超过 64", [_issue("document_too_deep", "OpenAPI 文档结构深度不能超过 64", path)])
    if isinstance(value, dict):
        identity = id(value)
        if identity in seen:
            raise OpenAPIContractError("OpenAPI 文档不能包含 YAML 别名循环", [_issue("document_cycle", "OpenAPI 文档不能包含 YAML 别名循环", path)])
        seen.add(identity)
        for key, item in value.items():
            if not isinstance(key, str):
                raise OpenAPIContractError("OpenAPI 对象键必须是字符串", [_issue("invalid_key", "OpenAPI 对象键必须是字符串", path)])
            child_path = f"{path}/{key}" if path else f"/{key}"
            if key == "$ref":
                if not isinstance(item, str) or not item.startswith("#"):
                    raise OpenAPIContractError("只允许本地 $ref", [_issue("external_ref", "只允许本地 $ref", child_path)])
                refs.append((item, child_path))
            _walk(item, child_path, depth + 1, refs, seen)
        seen.remove(identity)
    elif isinstance(value, list):
        identity = id(value)
        if identity in seen:
            raise OpenAPIContractError("OpenAPI 文档不能包含 YAML 别名循环", [_issue("document_cycle", "OpenAPI 文档不能包含 YAML 别名循环", path)])
        seen.add(identity)
        for index, item in enumerate(value):
            _walk(item, f"{path}/{index}", depth + 1, refs, seen)
        seen.remove(identity)


def _pointer(document: dict[str, Any], reference: str) -> Any:
    if reference == "#":
        return document
    if not reference.startswith("#/"):
        raise KeyError(reference)
    value: Any = document
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and token.isdigit() and int(token) < len(value):
            value = value[int(token)]
        else:
            raise KeyError(reference)
    return value


def _resolve(value: Any, document: dict[str, Any], stack: tuple[str, ...] = ()) -> Any:
    if not isinstance(value, dict) or "$ref" not in value or not isinstance(value["$ref"], str):
        return value
    reference = value["$ref"]
    if reference in stack:
        # 循环引用只用于校验安全性，不能递归展开。
        return {key: item for key, item in value.items() if key != "$ref"}
    target = _pointer(document, reference)
    resolved = _resolve(target, document, stack + (reference,))
    if not isinstance(resolved, dict):
        return resolved
    merged = dict(resolved)
    merged.update({key: item for key, item in value.items() if key != "$ref"})
    return merged


def _normalize_nullable(value: Any, *, from_openapi_30: bool = False, path: str = "") -> Any:
    if isinstance(value, list):
        return [_normalize_nullable(item, from_openapi_30=from_openapi_30, path=f"{path}/{index}") for index, item in enumerate(value)]
    if not isinstance(value, dict):
        return value
    normalized = {key: _normalize_nullable(item, from_openapi_30=from_openapi_30, path=f"{path}/{key}" if path else f"/{key}") for key, item in value.items() if key != "nullable"}
    if from_openapi_30:
        for bound, exclusive in (("minimum", "exclusiveMinimum"), ("maximum", "exclusiveMaximum")):
            flag = value.get(exclusive)
            if isinstance(flag, bool):
                if not flag:
                    normalized.pop(exclusive, None)
                else:
                    number = value.get(bound)
                    if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(float(number)):
                        raise OpenAPIContractError("无法无损归一化 OpenAPI 3.0.3 排他边界", [_issue("lossy_normalization", "无法无损归一化 OpenAPI 3.0.3 排他边界", f"{path}/{exclusive}" if path else f"/{exclusive}")])
                    normalized.pop(bound, None)
                    normalized[exclusive] = number
    if value.get("nullable") is True:
        schema = dict(normalized)
        schema_type = schema.get("type")
        if isinstance(schema_type, str):
            schema["type"] = [schema_type, "null"]
            normalized = schema
        elif isinstance(schema_type, list):
            schema["type"] = list(dict.fromkeys([*schema_type, "null"]))
            normalized = schema
        else:
            normalized = {"anyOf": [schema, {"type": "null"}]}
    return normalized


def _validate_version(document: dict[str, Any]) -> None:
    if "swagger" in document or document.get("openapi") == "2.0":
        raise OpenAPIContractError("不支持 Swagger 2.0", [_issue("unsupported_version", "不支持 Swagger 2.0", "/openapi")])
    version = document.get("openapi")
    if not isinstance(version, str):
        raise OpenAPIContractError("缺少有效的 OpenAPI 版本", [_issue("invalid_version", "缺少有效的 OpenAPI 版本", "/openapi")])
    parts = version.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise OpenAPIContractError("OpenAPI 版本格式无效", [_issue("invalid_version", "OpenAPI 版本格式无效", "/openapi")])
    major, minor, patch = (int(part) for part in parts)
    if (major, minor, patch) == (3, 0, 3) or (major, minor) == (3, 1):
        return
    raise OpenAPIContractError("只接受 OpenAPI 3.0.3 和 3.1.x", [_issue("unsupported_version", "只接受 OpenAPI 3.0.3 和 3.1.x", "/openapi")])


def _validate_operation_id_constraints(document: dict[str, Any]) -> None:
    """在通用规范校验前固定识别 operationId 身份错误。"""
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return
    seen: set[str] = set()
    for path in sorted(paths):
        path_item = _resolve(paths[path], document)
        if not isinstance(path_item, dict):
            continue
        for key, value in path_item.items():
            method = str(key).lower()
            if method not in METHOD_RANK:
                continue
            operation = _resolve(value, document)
            if not isinstance(operation, dict):
                continue
            operation_path = f"/paths/{path}/{method}"
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id.strip():
                raise OpenAPIContractError("每个 operation 都必须有非空 operationId", [_issue("missing_operation_id", "每个 operation 都必须有非空 operationId", operation_path)])
            operation_id = operation_id.strip()
            if operation_id in seen:
                raise OpenAPIContractError("Revision 内 operationId 必须唯一", [_issue("duplicate_operation_id", "Revision 内 operationId 必须唯一", operation_path, operation_id=operation_id)])
            seen.add(operation_id)


def _operations(document: dict[str, Any]) -> list[ParsedOperation]:
    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise OpenAPIContractError("OpenAPI paths 必须是对象", [_issue("invalid_paths", "OpenAPI paths 必须是对象", "/paths")])
    result: list[ParsedOperation] = []
    seen_operation_ids: set[str] = set()
    for path in sorted(paths):
        if not isinstance(path, str) or not path.startswith("/"):
            continue
        path_item = _resolve(paths[path], document, (f"#/paths/{path.lstrip('/').replace('~', '~0').replace('/', '~1')}",))
        if not isinstance(path_item, dict):
            continue
        for method in sorted((key.lower() for key in path_item if key.lower() in METHOD_RANK), key=lambda item: METHOD_RANK[item]):
            operation = _resolve(path_item[method], document)
            if not isinstance(operation, dict):
                raise OpenAPIContractError("operation 必须是对象", [_issue("invalid_operation", "operation 必须是对象", f"/paths/{path}/{method}")])
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id.strip():
                raise OpenAPIContractError("每个 operation 都必须有非空 operationId", [_issue("missing_operation_id", "每个 operation 都必须有非空 operationId", f"/paths/{path}/{method}")])
            operation_id = operation_id.strip()
            if operation_id in seen_operation_ids:
                raise OpenAPIContractError("Revision 内 operationId 必须唯一", [_issue("duplicate_operation_id", "Revision 内 operationId 必须唯一", f"/paths/{path}/{method}", operation_id=operation_id)])
            seen_operation_ids.add(operation_id)
            tags = operation.get("tags", [])
            if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                raise OpenAPIContractError("operation tags 必须是字符串数组", [_issue("invalid_tags", "operation tags 必须是字符串数组", f"/paths/{path}/{method}")])
            operation_document = _canonical(operation)
            operation_serialized = json.dumps(operation_document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            result.append(ParsedOperation(method=method.upper(), path=path, operation_id=operation_id, summary=operation.get("summary") if isinstance(operation.get("summary"), str) else None, tags=sorted(set(tags)), operation=operation_document, operation_hash=hashlib.sha256(operation_serialized.encode("utf-8")).hexdigest()))
            if len(result) > MAX_OPERATIONS:
                raise OpenAPIContractError("OpenAPI operations 不能超过 500 个", [_issue("too_many_operations", "OpenAPI operations 不能超过 500 个", "/paths")])
    return result


def _transition_warnings(operations: Iterable[ParsedOperation], previous: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    old_by_route = {(str(item["method"]).upper(), str(item["path"])): str(item["operation_id"]) for item in previous}
    old_by_id = {str(item["operation_id"]): (str(item["method"]).upper(), str(item["path"])) for item in previous}
    warnings: list[dict[str, Any]] = []
    for operation in operations:
        route = (operation.method, operation.path)
        old_id = old_by_route.get(route)
        if old_id is not None and old_id != operation.operation_id:
            raise OpenAPIContractError("相同 method + path 的 operationId 不可变更", [_issue("operation_id_changed", "相同 method + path 的 operationId 不可变更", f"/paths/{operation.path}/{operation.method.lower()}", previous_operation_id=old_id, operation_id=operation.operation_id)])
        old_route = old_by_id.get(operation.operation_id)
        if old_route is not None and old_route != route:
            warnings.append(_issue("route_changed", "相同 operationId 的路由已变更", operation_id=operation.operation_id, previous_method=old_route[0], previous_path=old_route[1], method=operation.method, path=operation.path))
    return warnings


def generate_markdown(document: dict[str, Any], operations: Iterable[ParsedOperation]) -> str:
    """生成不依赖时间、环境和数据库标识的 Markdown。"""
    info = document.get("info") if isinstance(document.get("info"), dict) else {}
    title = str(info.get("title") or "OpenAPI 接口契约")
    version = str(info.get("version") or "")
    lines = [f"# {title}", "", "- OpenAPI：3.1.0", "- Profile：v1"]
    if version:
        lines.append(f"- 接口版本：{version}")
    lines.extend(["", "## 服务器", ""])
    servers = document.get("servers") if isinstance(document.get("servers"), list) else []
    if servers:
        for server in sorted((item for item in servers if isinstance(item, dict)), key=lambda item: str(item.get("url", ""))):
            url = str(server.get("url", ""))
            description = server.get("description")
            lines.append(f"- `{url}`" + (f"：{description}" if isinstance(description, str) and description else ""))
    else:
        lines.append("未声明服务器。")
    lines.extend(["", "## 鉴权", ""])
    security = document.get("security")
    if isinstance(security, list) and security:
        for requirement in sorted((item for item in security if isinstance(item, dict)), key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))):
            names = sorted(str(name) for name in requirement)
            lines.append(f"- {', '.join(f'`{name}`' for name in names) or '匿名'}")
    else:
        lines.append("未声明全局鉴权。")
    lines.extend(["", "## 接口列表", ""])
    ordered = sorted(operations, key=lambda item: (item.path, METHOD_RANK.get(item.method.lower(), 99), item.method))
    if not ordered:
        lines.append("暂无接口。")
    for operation in ordered:
        lines.append(f"### `{operation.method} {operation.path}`")
        lines.append(f"- operationId：`{operation.operation_id}`")
        if operation.summary:
            lines.append(f"- 摘要：{operation.summary.replace(chr(10), ' ')}")
        if operation.tags:
            lines.append(f"- 标签：{', '.join(operation.tags)}")
        request_body = operation.operation.get("requestBody")
        parameters = operation.operation.get("parameters")
        lines.extend(["", "#### 请求"])
        if isinstance(parameters, list) and parameters:
            lines.append("- 参数：")
            for parameter in sorted((item for item in parameters if isinstance(item, dict)), key=lambda item: (str(item.get("in", "")), str(item.get("name", "")))):
                lines.append(f"  - `{parameter.get('in', '')}` `{parameter.get('name', '')}`")
        if isinstance(request_body, dict):
            content = request_body.get("content")
            if isinstance(content, dict):
                lines.append(f"- Content-Type：{', '.join(sorted(str(media_type) for media_type in content))}")
            elif request_body:
                lines.append("- 请求体已声明。")
        if not ((isinstance(parameters, list) and parameters) or isinstance(request_body, dict)):
            lines.append("无请求参数或请求体。")
        lines.extend(["", "#### 响应"])
        responses = operation.operation.get("responses")
        if isinstance(responses, dict) and responses:
            for response_code in sorted(responses, key=str):
                response = responses[response_code]
                description = response.get("description") if isinstance(response, dict) else None
                lines.append(f"- `{response_code}`" + (f"：{description}" if isinstance(description, str) and description else ""))
        else:
            lines.append("未声明响应。")
        lines.append("")
    components = document.get("components")
    schemas = components.get("schemas") if isinstance(components, dict) else None
    lines.extend(["## 公共 Schema", ""])
    if isinstance(schemas, dict) and schemas:
        for name in sorted(schemas):
            schema_json = json.dumps(schemas[name], ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            lines.extend([f"### Schema `{name}`", "", "```json", schema_json, "```", ""])
    else:
        lines.append("未声明公共 Schema。")
    return "\n".join(lines).rstrip() + "\n"


def parse_and_normalize_openapi(filename: str, content: bytes, previous_operations: Iterable[dict[str, Any]] = ()) -> NormalizedOpenAPI:
    document = _decode_document(filename, content)
    refs: list[tuple[str, str]] = []
    _walk(document, "", 0, refs, set())
    for reference, path in refs:
        try:
            _pointer(document, reference)
        except KeyError as error:
            raise OpenAPIContractError("本地 $ref 无法解析", [_issue("unresolved_ref", "本地 $ref 无法解析", path, reference=reference)]) from error
    _validate_version(document)
    _reject_structural_features(document)
    _validate_operation_id_constraints(document)
    _validate_openapi_spec(document)
    normalized = _normalize_nullable(document, from_openapi_30=document.get("openapi") == "3.0.3")
    if not isinstance(normalized, dict):
        raise OpenAPIContractError("OpenAPI 文档根节点必须是对象")
    normalized["openapi"] = "3.1.0"
    normalized = _canonical(normalized)
    operations = _operations(normalized)
    warnings = _transition_warnings(operations, previous_operations)
    serialized = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return NormalizedOpenAPI(document, str(document["openapi"]), normalized, "3.1.0", digest, operations, [], warnings, generate_markdown(normalized, operations))


# 提供便于单元测试和外部调用的简短别名。
normalize_openapi_document = parse_and_normalize_openapi
parse_openapi = parse_and_normalize_openapi


__all__ = [
    "MAX_DOCUMENT_BYTES",
    "MAX_OPERATIONS",
    "MAX_DEPTH",
    "OpenAPIContractError",
    "ParsedOperation",
    "NormalizedOpenAPI",
    "parse_and_normalize_openapi",
    "normalize_openapi_document",
    "parse_openapi",
    "generate_markdown",
]
