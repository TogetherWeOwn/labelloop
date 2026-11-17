import { afterEach, beforeEach, describe, expect, expectTypeOf, it, vi } from 'vitest'
import { AnnotationReadError, readAnnotation, readAnnotationHistory } from './annotations'
import type { AnnotationSnapshot } from './annotations'

const project = '8d72f6e1-d2ce-4e37-a6a2-0ad7c810e1b1'
const asset = '2e04f77c-8be1-492e-9e40-923f762d6557'
const origin = 'https://fixture.invalid'
const base = `${origin}/api/projects/${project}/assets/${asset}`
const snapshot: AnnotationSnapshot = {
  project_key: '项目/session/a',
  asset_key: 'session/frame%2F?#',
  source_sha256: 'a'.repeat(64),
  width: 200,
  height: 100,
  annotation: {
    revision: 4,
    parent_revision: 3,
    actor_kind: 'system',
    actor_id: 'fixture-policy',
    created_at: '2026-11-08T00:00:00Z',
    objects: [],
    label_state: 'identified',
    approval_state: 'draft',
    review_tier: 'machine_verified_no_human_review',
    scope_completeness: 'unreviewed',
    recorded_training_eligible: false,
    recorded_gold_eligible: false,
  },
}

beforeEach(() => {
  vi.stubGlobal('location', { origin })
})
afterEach(() => {
  vi.unstubAllGlobals()
})

function respond(body: unknown, status = 200) {
  const fetch = vi.fn().mockResolvedValue(Response.json(body, { status }))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('generated annotation consumer', () => {
  it('uses same-origin session reads and keeps opaque IDs separate from canonical keys', async () => {
    const fetch = respond(snapshot)
    const result = await readAnnotation(project, asset)
    expectTypeOf(result.annotation.revision).toEqualTypeOf<number>()
    expect(result).toEqual(snapshot)
    expect(fetch).toHaveBeenCalledTimes(1)
    const request = fetch.mock.calls[0][0] as Request
    expect(request.url).toBe(`${base}/annotations`)
    expect(request.method).toBe('GET')
    expect(request.credentials).toBe('same-origin')
    expect(request.cache).toBe('no-store')
    expect(request.redirect).toBe('error')
    expect(request.headers.has('authorization')).toBe(false)
    expect(request.headers.has('cookie')).toBe(false)
    expect(request.headers.has('x-csrf-token')).toBe(false)
    expect(result.annotation.scope_completeness).toBe('unreviewed')
    expect(result.annotation.recorded_gold_eligible).toBe(false)
  })

  it('preserves the exclusive history cursor including an empty terminal page', async () => {
    const body = { revisions: [], next_before_revision: null }
    const fetch = respond(body)
    expect(await readAnnotationHistory(project, asset, { limit: 2, beforeRevision: 3 })).toEqual(body)
    const url = new URL((fetch.mock.calls[0][0] as Request).url)
    expect(url.pathname).toBe(`/api/projects/${project}/assets/${asset}/annotation-history`)
    expect(url.searchParams.get('limit')).toBe('2')
    expect(url.searchParams.get('before_revision')).toBe('3')
  })

  it('defaults history to 20 without an invented cursor', async () => {
    const fetch = respond({ revisions: [], next_before_revision: null })
    await readAnnotationHistory(project, asset)
    const url = new URL((fetch.mock.calls[0][0] as Request).url)
    expect(url.searchParams.get('limit')).toBe('20')
    expect(url.searchParams.has('before_revision')).toBe(false)
  })

  it.each(['../other', 'session/frame', 'x'.repeat(300), '项目', `${origin}/other`])(
    'refuses non-UUID lookup handles before fetch: %s', async (value) => {
      const fetch = respond(snapshot)
      await expect(readAnnotation(value, asset)).rejects.toMatchObject({ code: 'invalid_request' })
      await expect(readAnnotationHistory(project, value)).rejects.toMatchObject({ code: 'invalid_request' })
      expect(fetch).not.toHaveBeenCalled()
    },
  )

  it.each([
    { limit: 0 }, { limit: 101 }, { limit: 1.5 }, { limit: NaN },
    { beforeRevision: 0 }, { beforeRevision: Infinity }, { beforeRevision: 2.5 },
  ])('bounds history inputs without a network call: %j', async (page) => {
    const fetch = respond({})
    await expect(readAnnotationHistory(project, asset, page)).rejects.toMatchObject({ code: 'invalid_request' })
    expect(fetch).not.toHaveBeenCalled()
  })

  it.each([
    [401, 'authentication_required'], [403, 'permission_denied'],
    [404, 'annotation_not_found'], [422, 'invalid_request'],
    [503, 'annotations_unavailable'], [500, 'request_failed'],
  ])('redacts HTTP %i without retrying or rendering server text', async (status, code) => {
    const fetch = respond({ detail: 'fixture-private-driver-detail' }, status)
    await expect(readAnnotation(project, asset)).rejects.toMatchObject({
      name: 'AnnotationReadError', code, message: code, status,
    })
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it.each(['network', 'json', 'empty'])(
    'turns %s failures into a safe error without logging or retries', async (failure) => {
      const fetch = vi.fn()
      if (failure === 'network') fetch.mockRejectedValue(new Error('fixture-private-fetch-detail'))
      else fetch.mockResolvedValue(new Response(failure === 'empty' ? '' : 'fixture-invalid-json'))
      vi.stubGlobal('fetch', fetch)
      await expect(readAnnotationHistory(project, asset)).rejects.toMatchObject({
        code: 'request_failed', message: 'request_failed',
      })
      expect(fetch).toHaveBeenCalledTimes(1)
    },
  )

  it('refuses a null successful body', async () => {
    respond(null)
    await expect(readAnnotation(project, asset)).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('propagates cancellation and gives every request a timeout signal', async () => {
    const fetch = respond(snapshot)
    const controller = new AbortController()
    await readAnnotation(project, asset, controller.signal)
    const request = fetch.mock.calls[0][0] as Request
    expect(request.signal.aborted).toBe(false)
    controller.abort()
    expect(request.signal.aborted).toBe(true)
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('keeps safe error messages independent of raw errors', () => {
    expect(new AnnotationReadError('invalid_request').message).toBe('invalid_request')
  })
})
