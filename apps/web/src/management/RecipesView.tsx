import { useState } from 'react';
import type { RecipeId, RecipePreview, ScreenProps } from './contracts.ts';
import { Button, CommandButton, Download, Facts, Notice, PageHeading, ReadOnly, Table } from './shared.tsx';
import scene from './shelf-fixture.svg?url';

export function RecipesView({ data, actions }: ScreenProps) {
  const [selected, setSelected] = useState<readonly RecipeId[]>(data.recipes.map((recipe) => recipe.id));
  const [flagged, setFlagged] = useState(false);
  const visible = data.recipes.filter((recipe) => selected.includes(recipe.id));
  const release = data.release;
  return <>
    <PageHeading title="Size and crop recipes." gloss="See what the model will see." lede="Each recipe builds its own dataset from the original pixels. Check small targets and clipped edges before compilation.">
      <CommandButton actions={actions} primary reason={!release || release.state !== 'Frozen' ? 'Freeze the canonical release first.' : !selected.length ? 'Select at least one recipe.' : undefined} onClick={() => { if (release) void actions.send({ kind: 'compile', release_id: release.id, recipe_ids: selected }); }}>Compile {release?.id ?? 'release'} with {selected.length} recipes</CommandButton>
    </PageHeading><ReadOnly role={actions.role} />
    {!release && <Notice tone="info">Compile needs a release. Freeze a release first. <a href="?screen=releases">Open releases</a></Notice>}
    {data.recipes.length === 0 ? <Notice tone="info">No recipes to preview. Start with the two full-frame defaults after project setup.</Notice> : <>
      <div className="seg" role="group" aria-label="Recipes to compare">{data.recipes.map((recipe) => <Button key={recipe.id} aria-pressed={selected.includes(recipe.id)} onClick={() => setSelected((ids) => ids.includes(recipe.id) ? ids.filter((id) => id !== recipe.id) : [...ids, recipe.id])}>{recipe.id}</Button>)}</div>
      <section className="card"><header><h2>Preview · {visible[0]?.source_id ?? 'Choose a recipe'}</h2><div className="acts"><Button onClick={() => setFlagged(false)}>Previous</Button><Button onClick={() => setFlagged(true)}>Next flagged image</Button></div></header>
        {flagged && <Notice tone="info">This recorded fixture has one source image. Its below-minimum target is listed beneath the 320 frame.</Notice>}
        <div className="frames">{visible.map((recipe) => <RecipeFrame key={recipe.id} recipe={recipe} fixture={data.provenance === 'fixture'} />)}</div>
        <p className="label">Both shown at one shared scale · thick red box = flagged target · hatched = padding (grey 114 in the output)</p>
        {data.provenance !== 'fixture' && <Notice tone="info">Source-pixel delivery must be supplied by the management facade. The invented fixture scene is not a live image preview.</Notice>}
      </section>
      <div className="grid2 even"><section className="card"><h2>Across {release?.id ?? 'the recorded release'} · {release?.images ?? 0} images</h2><Table caption="Flagged targets per recipe" columns={['Recipe', 'At or below 6 px', 'Under 4 px', 'Clipped', 'Images excluded']}>
        {visible.map((recipe) => <tr key={recipe.id}><th scope="row">{recipe.side}</th><td>{recipe.flags.tiny}</td><td>{recipe.flags.below_minimum}</td><td>{recipe.flags.clipped}</td><td>{recipe.flags.excluded_images}</td></tr>)}
      </Table>{visible.filter((recipe) => recipe.flags.excluded_images > 0).map((recipe) => <Notice key={recipe.id} tone="danger">The {recipe.side} recipe would drop {recipe.flags.excluded_images} images. Consider a tile recipe, or explicitly qualify a narrower scope. Visible in-scope fragments never become unlabelled background.</Notice>)}</section>
      <div className="stack">{visible.map((recipe) => <section className="card" key={recipe.id}><h2>{recipe.id}</h2><Facts items={[[ 'Field of view', 'Full original frame' ], ['Resize', 'Letterbox, centred'], ['Down / up', 'area / linear'], ['Padding', '114, 114, 114'], ['Smallest target / flag threshold', '4 px / 6 px'], ['Version', recipe.locked ? 'v1 · locked by a frozen release' : 'v1 · draft'], ['Recorded raster gains', `${recipe.sx} / ${recipe.sy}`], ['Transform record', recipe.transform_record]]} />
        <CommandButton actions={actions} onClick={() => { void actions.send({ kind: 'new_recipe_version', recipe_id: recipe.id }); }}>{recipe.locked ? 'Make v2' : 'Edit recipe'}</CommandButton>
        {recipe.job && <p><span className={`tag ${recipe.job === 'Failed' ? 'danger' : ''}`}>{recipe.job}</span> · {recipe.id}</p>}
        {recipe.failures.map((failure) => <Notice key={failure} tone="danger">{failure}</Notice>)}
        <Download name={`${recipe.id}-transforms.json`} value={recipe}>Download recorded transform</Download>
        {recipe.job === 'Completed' && <><Download name={recipe.manifest} value={{ fixture: data.provenance === 'fixture', recipe }}>Variant manifest</Download><Download name={recipe.validation} value={{ failures: recipe.failures }}>Validation report</Download></>}
      </section>)}</div></div>
    </>}
  </>;
}

function RecipeFrame({ recipe, fixture }: { recipe: RecipePreview; fixture: boolean }) {
  return <figure className={`frame frame-${recipe.side}`} data-recipe={recipe.id}>
    <svg className="recipe-image" viewBox={`0 0 ${recipe.side} ${recipe.side}`} role="img" aria-label={`${recipe.side} recipe: original ${recipe.source_width} by ${recipe.source_height}, output ${recipe.side} by ${recipe.side}, picture ${recipe.resized_width} by ${recipe.resized_height}, padding ${recipe.padding.join(', ')} pixels. Boxes use ${recipe.transform_record}.`}>
      {fixture && <image href={scene} x={recipe.padding[0]} y={recipe.padding[1]} width={recipe.resized_width} height={recipe.resized_height} />}
      {recipe.boxes.map((box) => <rect key={box.id} data-object={box.id} className={`preview-box ${box.flag === 'none' ? 'accepted' : 'flagged'}`} x={box.xyxy[0]} y={box.xyxy[1]} width={box.xyxy[2] - box.xyxy[0]} height={box.xyxy[3] - box.xyxy[1]} vectorEffect="non-scaling-stroke"><title>{box.class_name} · {box.provenance} · {box.consequence}</title></rect>)}
    </svg>
    <figcaption className="small"><strong>{recipe.side} recipe</strong> · {recipe.side} × {recipe.side} · picture {recipe.resized_width} × {recipe.resized_height} · padding {recipe.padding.join('/')} px · scales {recipe.sx.toFixed(3)} / {recipe.sy.toFixed(3)}</figcaption>
    <ul className="hist">{recipe.boxes.map((box) => <li key={box.id}><span className={`tag ${box.flag === 'exclude' ? 'danger' : box.flag === 'warn' ? 'warn' : ''}`}>{box.class_name} · {box.provenance}</span><span>{box.consequence}</span></li>)}</ul>
  </figure>;
}
