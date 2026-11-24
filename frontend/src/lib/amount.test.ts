import { describe, expect, it } from 'vitest';
import { money, press, value } from './amount';
import { todaySG } from './dates';

const type = (keys: string[]) => keys.reduce(press, '');

describe('keypad amount', () => {
  it('builds amounts', () => {
    expect(type(['4', '.', '5'])).toBe('4.5');
    expect(value('4.5')).toBe(4.5);
    expect(type(['.', '5'])).toBe('0.5');
  });
  it('allows one point and two decimals', () => {
    expect(type(['1', '.', '.', '2', '3', '4'])).toBe('1.23');
  });
  it('drops leading zeros and caps length', () => {
    expect(type(['0', '0', '7'])).toBe('7');
    expect(type(['9', '9', '9', '9', '9', '9', '9', '9'])).toBe('9999999');
  });
  it('deletes', () => {
    expect(type(['1', '2', 'back'])).toBe('1');
    expect(value('')).toBe(0);
  });
  it('formats', () => {
    expect(money(-4.5, 'SGD', true)).toBe('−S$4.50');
    expect(money(50, 'MYR', true)).toBe('+RM50.00');
  });
});

describe('dates', () => {
  it('uses Singapore time', () => {
    // 20:30 UTC on 23 Nov is 04:30 on 24 Nov in Singapore
    expect(todaySG(new Date('2026-11-23T20:30:00Z'))).toBe('2026-11-24');
  });
});
