import { describe, expect, it } from 'vitest'

import { describeCoverPoolStatus } from './coverStatus'

describe('cover pool status helper', () => {
  it('formats ready cover pool counts', () => {
    expect(
      describeCoverPoolStatus({
        ok: true,
        cover_dir: '/tmp/covers',
        paths: ['/tmp/covers/cover_01.png', '/tmp/covers/cover_02.png'],
        count: 2,
        error: null,
      }),
    ).toBe('已就绪 · 2 张')
  })

  it('adds action guidance when the cover pool is unavailable', () => {
    expect(
      describeCoverPoolStatus({
        ok: false,
        cover_dir: '/tmp/covers',
        paths: [],
        count: 0,
        error: '封面目录不存在: /tmp/covers',
      }),
    ).toContain('未就绪')
  })
})
