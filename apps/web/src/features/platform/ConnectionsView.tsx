import { checkedTime, fingerprintText } from './contracts.ts';
import type { PlatformSnapshot, ViewerRole } from './contracts.ts';

export function ConnectionsView({ role, snapshot, now }: { role: ViewerRole; snapshot: PlatformSnapshot; now: number }) {
  if (role !== 'admin') return <p>Only an admin can manage connections.</p>;
  const { connection, providers } = snapshot;
  return <>
    <div className="phead"><div><h1>Connections. <span className="g">Keys stay on the server.</span></h1>
      <p className="lede">labelloop never shows a key again after you save it. You see a fingerprint so you can tell keys apart.</p></div></div>
    <div className="grid2"><div className="stack">
      <section className="card" aria-labelledby="platform-heading">
        <header><h2 id="platform-heading">Ultralytics Platform</h2><span className={`tag ${connection.state === 'connected' ? 'ok' : 'warn'}`}>
          {connection.state === 'connected' ? 'Connected' : connection.state === 'check_failed' ? 'Check failed' : 'Not connected'}</span></header>
        {connection.workspace && <dl className="facts"><dt>Workspace ID</dt><dd className="mono">{connection.workspace.id}</dd>
          <dt>Workspace slug</dt><dd className="mono">{connection.workspace.slug}</dd><dt>Owner</dt><dd>{connection.workspace.owner}</dd>
          <dt>Checked</dt><dd><time dateTime={connection.checkedAt}>{checkedTime(connection.checkedAt, now)}</time></dd></dl>}
        {connection.fingerprint && <div className="secret" aria-label="Key fingerprint">{fingerprintText(connection.fingerprint)}</div>}
        {connection.state === 'check_failed' && <p className="notice warn">The workspace check failed. Ask an admin to check the connection when authenticated integration is available.</p>}
        <p className="notice warn">This key has full owner rights on the workspace and never expires. labelloop only uses it for the operations below, and every paid one goes through Costs.</p>
        {connection.state === 'not_connected' ? <>
          <div className="field"><label htmlFor="platform-key">Paste a Platform API key</label>
            <input id="platform-key" className="input" type="password" autoComplete="off" spellCheck={false} disabled aria-describedby="key-help" />
            <p id="key-help" className="help">Create a named key in Ultralytics Platform → Settings → API keys. It starts with ul_. Key entry is disabled in this fixture preview.</p></div>
          <div className="field"><label htmlFor="workspace">Workspace returned by key validation</label><select id="workspace" className="input" disabled><option>Check a key first</option></select></div>
          <button className="pill" disabled>Connect workspace</button>
        </> : <div className="acts"><button className="pill ghost" disabled>Replace key</button><button className="pill ghost" disabled>Check now</button><button className="pill danger" disabled>Disconnect</button></div>}
      </section>
      <section className="card" aria-labelledby="providers-heading"><header><h2 id="providers-heading">LLM review providers</h2><button className="pill ghost" disabled>Add provider</button></header>
        {providers.length ? <div className="tablewrap" tabIndex={0} role="region" aria-label="Providers, scrolls sideways"><table><caption className="sr">LLM providers</caption>
          <thead><tr><th scope="col">Provider and model</th><th scope="col">Key fingerprint</th><th scope="col">Fixture check</th><th scope="col">Sees images</th></tr></thead>
          <tbody>{providers.map((provider) => <tr key={provider.id}><th scope="row">{provider.name} · {provider.model}</th>
            <td className="mono">{provider.fingerprint ? fingerprintText(provider.fingerprint) : 'No key'}</td>
            <td><span className={`tag ${provider.probe === 'passed' ? 'ok' : 'danger'}`}>{provider.probe === 'passed' ? 'Passed' : 'Failed'}</span>{provider.probe === 'failed' && ' Schema check failed; not used.'}</td>
            <td>{provider.probe === 'failed' ? 'Not used until probe passes' : provider.imagesAllowed ? provider.local ? 'On this server' : 'Allowed by policy' : 'No'}</td></tr>)}</tbody></table></div> : <p>No providers configured. An admin can add a provider after integration.</p>}
        <p className="small t2">A provider that fails its fixture check isn't used until it passes again.</p>
      </section>
    </div><aside className="stack" aria-label="Connection policy">
      <section className="card"><h2>What labelloop may do here</h2><ul className="checks" role="list">
        {([
          ['Upload and check releases · disabled in preview', true],
          ['Start training runs · with your approval, disabled in preview', true],
          ['Download weights · disabled in preview', true],
          ['Batch auto-annotation · off by policy', false],
          ['Delete datasets · never', false],
        ] as const).map(([label, allowed]) => <li key={label}><span className={allowed ? 'ok-i' : 'todo-i'}>
          <svg className="ico" viewBox="0 0 24 24" aria-hidden="true"><path d={allowed ? 'm5 12.5 4.5 4.5L19 7.5' : 'm7 7 10 10M17 7 7 17'} /></svg>
        </span><span>{label}</span></li>)}
      </ul><p className="small t2">Workspace connectivity and validation are not permission to train.</p></section>
      <section className="card"><h2>Who may see this project's images</h2><p className="small t2">{snapshot.permittedUse}</p>
        {providers.map((provider) => <div className="toggle" key={provider.id}><span>{provider.name}</span>
          <button className="switch" role="switch" aria-checked={provider.imagesAllowed} disabled aria-label={`${provider.name} may see images`} /></div>)}
        <p className="help">Policy changes are disabled until authenticated integration. Turning a provider off will apply to new requests.</p>
      </section>
    </aside></div>
  </>;
}
