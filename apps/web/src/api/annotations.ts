import createClient from 'openapi-fetch'
import type { components, paths } from './schema'

export type AnnotationSnapshot = components['schemas']['AnnotationSnapshot']
export type AnnotationHistory = components['schemas']['AnnotationHistory']

export class AnnotationReadError extends Error {
  constructor(readonly code: string, readonly status: number = 0) {
    super(code)
    this.name = 'AnnotationReadError'
  }
}

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function pathIds(projectId: string, assetId: string) {
  if (!uuid.test(projectId) || !uuid.test(assetId)) {
    throw new AnnotationReadError('invalid_request')
  }
  return { project_id: projectId, asset_id: assetId }
}

function client() {
  // No caller/model-controlled origin, bearer token, headers or redirect policy.
  return createClient<paths>({
    baseUrl: `${globalThis.location.origin}/api`,
    credentials: 'same-origin',
    cache: 'no-store',
    redirect: 'error',
  })
}

function requestSignal(signal?: AbortSignal) {
  const timeout = AbortSignal.timeout(15_000)
  return signal ? AbortSignal.any([signal, timeout]) : timeout
}

async function result<T>(request: Promise<{ data?: T; response: Response }>): Promise<T> {
  try {
    const { data, response } = await request
    if (!response.ok) {
      const codes: Record<number, string> = {
        401: 'authentication_required',
        403: 'permission_denied',
        404: 'annotation_not_found',
        422: 'invalid_request',
        503: 'annotations_unavailable',
      }
      throw new AnnotationReadError(codes[response.status] ?? 'request_failed', response.status)
    }
    if (data === undefined || data === null) throw new AnnotationReadError('invalid_response')
    return data
  } catch (error) {
    if (error instanceof AnnotationReadError) throw error
    // Never render or log raw fetch/JSON/backend errors (or retry an unauthorized read).
    throw new AnnotationReadError('request_failed')
  }
}

export async function readAnnotation(
  projectId: string,
  assetId: string,
  signal?: AbortSignal,
): Promise<AnnotationSnapshot> {
  const path = pathIds(projectId, assetId)
  return result(client().GET('/projects/{project_id}/assets/{asset_id}/annotations', {
    params: { path },
    signal: requestSignal(signal),
  }))
}

export async function readAnnotationHistory(
  projectId: string,
  assetId: string,
  page: { limit?: number; beforeRevision?: number } = {},
  signal?: AbortSignal,
): Promise<AnnotationHistory> {
  const path = pathIds(projectId, assetId)
  const { limit = 20, beforeRevision } = page
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100 ||
      (beforeRevision !== undefined && (!Number.isSafeInteger(beforeRevision) || beforeRevision < 1))) {
    throw new AnnotationReadError('invalid_request')
  }
  return result(client().GET('/projects/{project_id}/assets/{asset_id}/annotation-history', {
    params: { path, query: { limit, before_revision: beforeRevision } },
    signal: requestSignal(signal),
  }))
}
