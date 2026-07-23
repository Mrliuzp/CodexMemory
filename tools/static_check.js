const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const requiredFiles = [
  "alembic.ini",
  "alembic/env.py",
  "docker-compose.yml",
  "README.md",
  "src/codex_memory/persistence/config.py",
  "src/codex_memory/persistence/db.py",
  "src/codex_memory/api/v1_app.py",
  "src/codex_memory/entrypoints/worker.py",
];

let failures = 0;
for (const relative of requiredFiles) {
  if (!fs.existsSync(path.join(root, relative))) {
    console.error(`缺少必需文件：${relative}`);
    failures += 1;
  }
}

const scanRoots = ["src", "tests", "alembic"];
const scanFiles = [];
function collect(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "__pycache__") collect(full);
    } else if (/\.(py|ini)$/.test(entry.name)) {
      scanFiles.push(full);
    }
  }
}
for (const relative of scanRoots) collect(path.join(root, relative));
scanFiles.push(path.join(root, "README.md"), path.join(root, "start-local.ps1"), path.join(root, "alembic.ini"));

const removedDatabaseName = ["sql", "ite"].join("");
for (const file of scanFiles) {
  const text = fs.readFileSync(file, "utf8");
  if (text.toLowerCase().includes(removedDatabaseName)) {
    console.error(`发现已移除数据库实现的残留：${path.relative(root, file)}`);
    failures += 1;
  }
  if (text.includes("\uFFFD")) {
    console.error(`发现 Unicode 替换字符：${path.relative(root, file)}`);
    failures += 1;
  }
}

const configText = fs.readFileSync(path.join(root, "src/codex_memory/persistence/config.py"), "utf8");
if (!configText.includes("postgresql+psycopg://")) {
  console.error("默认数据库 URL 不是 PostgreSQL");
  failures += 1;
}

const composeText = fs.readFileSync(path.join(root, "docker-compose.yml"), "utf8");
for (const marker of ["pgvector/pgvector:pg16", "CODEX_MEMORY_DATABASE_URL"]) {
  if (!composeText.includes(marker)) {
    console.error(`Compose 缺少 PostgreSQL 契约：${marker}`);
    failures += 1;
  }
}

if (failures > 0) {
  process.exitCode = 1;
} else {
  console.log("static_check: ok");
}
