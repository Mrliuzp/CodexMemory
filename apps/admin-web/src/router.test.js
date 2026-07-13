import { describe, expect, it } from 'vitest'
import router from './router'

describe('admin navigation', () => {
  it('keeps the P0 read-only entry points addressable', () => {
    const paths = router.getRoutes().map((route) => route.path)
    expect(paths).toEqual(expect.arrayContaining(['/dashboard', '/projects', '/records']))
  })
})
