import { describe, expect, it } from 'vitest';
import { App } from './App.tsx';

describe('web bootstrap', () => {
  it('exports the application component', () => {
    expect(App.name).toBe('App');
  });
});
