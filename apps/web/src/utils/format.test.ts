import { describe, expect, it } from 'vitest'
import { formatDuration, formatTimecode, parseTimecode } from './format'

describe('timecode formatting', () => {
  it('formats positions consistently, including hours and zero', () => {
    expect(formatTimecode(0)).toBe('00:00:00.0')
    expect(formatTimecode(3723.56)).toBe('01:02:03.6')
  })

  it('formats clip lengths separately from positions', () => {
    expect(formatDuration(83.6)).toBe('01:23.6')
  })

  it('parses timecodes and legacy seconds and rejects invalid input', () => {
    expect(parseTimecode('01:23.6')).toBeCloseTo(83.6)
    expect(parseTimecode('01:02:03.5')).toBeCloseTo(3723.5)
    expect(parseTimecode(83.6)).toBeCloseTo(83.6)
    expect(() => parseTimecode('not-a-time')).toThrow('Invalid time value')
  })
})
