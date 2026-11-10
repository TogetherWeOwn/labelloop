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

  it('reads historical reference recovery pins without mutation authority', async () => {
    const { request, result } = mockRequest();
    const csrf = vi.fn(() => 'csrf');
    const client = new ManagementClient(csrf);
    await expect(client.readReferenceLifecycle({ project_id: 'project/../other' }))
      .resolves.toEqual(result);
    expect(request).toHaveBeenCalledWith(
      '/api/projects/project%2F..%2Fother/references/lifecycle',
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

  it('transports source declarations without assigning identity or splits', async () => {
    const { request } = mockRequest();
    const client = new ManagementClient(() => 'csrf');
    const body = {
      expected_revision: 1,
      original_base64: 'Zml4dHVyZQ==',
      source_group: 'capture',
      source_metadata: { session: 'session-1', sequence: 'sequence-2', subject: 'subject-3' },
    };
    await client.ingestAsset({ project_id: 'p', 'idempotency-key': 'source-1', body });
    expect(request).toHaveBeenCalledWith('/api/projects/p/assets', {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-CSRF-Token': 'csrf',
        'Idempotency-Key': 'source-1',
      },
      credentials: 'same-origin',
      cache: 'no-store',
      redirect: 'error',
      body: JSON.stringify(body),
    });
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

  it('saves typed guide intent without completeness, recipe or approval authority', async () => {
    const { request } = mockRequest();
    const client = new ManagementClient(() => 'csrf');
    const body = {
      expected_revision: 3,
      annotation_guide: 'Visible birds only',
      extent: 'visible' as const,
      truncation: 'Clip at image edges',
      occlusion: 'Visible extent',
      reflections: 'Out of scope',
      min_target_px: 4,
      exhaustive_for_scope: true,
      background: 'Explicit out-of-scope objects',
    };
    await client.saveGuideDraft({ project_id: 'p', 'idempotency-key': 'guide-1', body });
    expect(request).toHaveBeenCalledWith('/api/projects/p/setup/guide', {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-CSRF-Token': 'csrf',
        'Idempotency-Key': 'guide-1',
      },
      credentials: 'same-origin',
      cache: 'no-store',
      redirect: 'error',
      body: JSON.stringify(body),
    });
  });

  it('saves ontology-pinned class intent without assigning released identities', async () => {
    const { request } = mockRequest();
    const client = new ManagementClient(() => 'csrf');
    const body = {
      expected_revision: 4,
      base_ontology_id: 'ontology-1',
      classes: [{
        stable_id: 'bird', name: 'Bird', definition: 'A visible bird',
        aliases: ['Avian'], parent_id: null, positive_examples: ['Perched bird'],
        exclusions: ['Printed depictions'], mirror_safe: false,
      }],
    };
    await client.saveClassCatalogDraft({ project_id: 'p', 'idempotency-key': 'classes-1', body });
    expect(request).toHaveBeenCalledWith('/api/projects/p/setup/classes', {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-CSRF-Token': 'csrf',
        'Idempotency-Key': 'classes-1',
      },
      credentials: 'same-origin',
      cache: 'no-store',
      redirect: 'error',
      body: JSON.stringify(body),
    });
  });

  it('enrolls proposed reference metadata against server-owned source and version pins', async () => {
    const { request } = mockRequest();
    const client = new ManagementClient(() => 'csrf');
    const body = {
      expected_revision: 2, base_ontology_id: 'ontology-1', assignment_version: 'assignment-1',
      asset_id: 'source-a', class_id: 'bird', source: 'Generated fixture', permitted_use: 'CC0',
      view: 'Side', lighting: 'Diffuse', scale: 'Full frame', validity_scope: 'Fixture',
      confused_with: [],
    };
    await client.enrollReference({ project_id: 'p', 'idempotency-key': 'reference-1', body });
    expect(request).toHaveBeenCalledWith('/api/projects/p/references', {
      method: 'POST',
      headers: {
        Accept: 'application/json', 'Content-Type': 'application/json',
        'X-CSRF-Token': 'csrf', 'Idempotency-Key': 'reference-1',
      },
      credentials: 'same-origin', cache: 'no-store', redirect: 'error', body: JSON.stringify(body),
    });
  });

  it('pins first grouping without choosing splits, thresholds or source observations', async () => {
    const { request } = mockRequest();
    const client = new ManagementClient(() => 'csrf');
    const body = {
      expected_revision: 6, ontology_id: 'ontology-1', source_policy_sha256: 'a'.repeat(64),
      annotation_revisions: { 'source-a': 1, 'source-b': 0 },
    };
    await client.publishInitialSourceGrouping({ project_id: 'p', 'idempotency-key': 'group-1', body });
    expect(request).toHaveBeenCalledWith('/api/projects/p/source-assignments/initial', {
      method: 'POST',
      headers: {
        Accept: 'application/json', 'Content-Type': 'application/json',
        'X-CSRF-Token': 'csrf', 'Idempotency-Key': 'group-1',
      },
      credentials: 'same-origin', cache: 'no-store', redirect: 'error', body: JSON.stringify(body),
    });
  });

  it('resolves a server pair ID without supplying endpoints or split claims', async () => {
    const { request } = mockRequest();
    const client = new ManagementClient(() => 'csrf');
    const body = {
      expected_revision: 3, pair_id: 'pair-a', decision: 'related' as const,
      reason: 'Reviewed source relationship',
    };
    await client.resolveSourcePair({ project_id: 'p', 'idempotency-key': 'pair-1', body });
    expect(request).toHaveBeenCalledWith('/api/projects/p/source-pairs/resolve', {
      method: 'POST',
      headers: {
        Accept: 'application/json', 'Content-Type': 'application/json',
        'X-CSRF-Token': 'csrf', 'Idempotency-Key': 'pair-1',
      },
      credentials: 'same-origin', cache: 'no-store', redirect: 'error', body: JSON.stringify(body),
    });
  });

  it('saves source policy intent without file, grouping or split authority', async () => {
    const { request } = mockRequest();
    const client = new ManagementClient(() => 'csrf');
    const body = {
      expected_revision: 5,
      metadata_keys: ['session', 'sequence'] as ('session' | 'sequence')[],
      collection_notes: 'Independent capture sessions',
    };
    await client.saveSourcePolicyDraft({ project_id: 'p', 'idempotency-key': 'sources-1', body });
    expect(request).toHaveBeenCalledWith('/api/projects/p/setup/sources', {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-CSRF-Token': 'csrf',
        'Idempotency-Key': 'sources-1',
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
