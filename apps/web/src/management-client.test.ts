import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ManagementApiError,
  ManagementClient,
} from './generated/management-client';

// Actions generates this client before web checks; no handwritten API substitute.
afterEach(() => vi.unstubAllGlobals());

function mockRequest() {
  const result = { id: 'result' };
  const request = vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(result),
  });
  vi.stubGlobal('fetch', request);
  return { request, result };
}

describe('generated management client', () => {
  it('uses fixed same-origin transport and encodes path and query data', async () => {
    const { request, result } = mockRequest();
    const csrf = vi.fn(() => 'csrf');
    const client = new ManagementClient(csrf);
    await expect(client.readRegistry({
      project_id: 'project/../other',
      recipe_id: 'full-letterbox-320-v1',
      cursor: 'cursor&limit=100',
      limit: 10,
    })).resolves.toEqual(result);
    expect(request).toHaveBeenCalledWith(
      '/api/projects/project%2F..%2Fother/registry/full-letterbox-320-v1?cursor=cursor%26limit%3D100&limit=10',
      {
        method: 'GET',
        headers: { Accept: 'application/json' },
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'error',
        body: undefined,
      },
    );
    expect(csrf).not.toHaveBeenCalled();
  });

  it('sends authoritative freeze membership with CSRF and idempotency headers', async () => {
    const { request } = mockRequest();
    const client = new ManagementClient(() => 'csrf');
    const body = {
      expected_revision: 3,
      ontology_id: 'ontology-1',
      assignment_version: 'split-1',
      annotation_revisions: { 'asset-a': 2 },
    };
    await client.freezeRelease({ project_id: 'p', 'idempotency-key': 'freeze-1', body });
    expect(request).toHaveBeenCalledWith('/api/projects/p/dataset-releases', {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-CSRF-Token': 'csrf',
        'Idempotency-Key': 'freeze-1',
      },
      credentials: 'same-origin',
      cache: 'no-store',
      redirect: 'error',
      body: JSON.stringify(body),
    });
  });

  it('saves a typed scope draft without granting approval or publication', async () => {
    const { request } = mockRequest();
    const client = new ManagementClient(() => 'csrf');
    const body = {
      expected_revision: 2,
      name: 'Wildlife',
      scope: 'Visible birds',
      domain: 'wildlife',
      representation: 'scene_object' as const,
      intended_use: 'Fixture detection',
      specificity: 'Species',
      source_permitted_use: 'CC0 fixture',
    };
    await client.saveScopeDraft({ project_id: 'p', 'idempotency-key': 'scope-1', body });
    expect(request).toHaveBeenCalledWith('/api/projects/p/setup/scope', {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-CSRF-Token': 'csrf',
        'Idempotency-Key': 'scope-1',
      },
      credentials: 'same-origin',
      cache: 'no-store',
      redirect: 'error',
      body: JSON.stringify(body),
    });
  });

  it('uses the server training profile and reads a fresh CSRF token per mutation', async () => {
    const { request } = mockRequest();
    const csrf = vi.fn().mockReturnValueOnce('first').mockReturnValueOnce('second');
    const client = new ManagementClient(csrf);
    const body = { expected_revision: 4, training_profile_id: 'cpu-320-v1' };
    await client.startTraining({ variant_id: 'v', 'idempotency-key': 'train-1', body });
    await client.startTraining({ variant_id: 'v', 'idempotency-key': 'train-1', body });
    for (const [index, token] of ['first', 'second'].entries()) {
      expect(request).toHaveBeenNthCalledWith(index + 1, '/api/variants/v/training-runs', {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-CSRF-Token': token,
          'Idempotency-Key': 'train-1',
        },
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'error',
        body: JSON.stringify(body),
      });
    }
  });

  it('does not retry conflicts or reflect server error bodies', async () => {
    const json = vi.fn().mockResolvedValue({ detail: 'private-server-diagnostic' });
    const request = vi.fn().mockResolvedValue({ ok: false, status: 409, json });
    vi.stubGlobal('fetch', request);
    const client = new ManagementClient(() => 'csrf');
    const operation = client.compileVariant({
      release_id: 'r',
      'idempotency-key': 'compile-1',
      body: { expected_revision: 2, recipe_id: 'full-letterbox-320-v1' },
    });
    await expect(operation).rejects.toBeInstanceOf(ManagementApiError);
    await expect(operation).rejects.toMatchObject({
      status: 409,
      message: 'Management request failed (409)',
    });
    expect(request).toHaveBeenCalledTimes(1);
    expect(json).not.toHaveBeenCalled();
  });
});
