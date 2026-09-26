<svelte:options customElement={{ tag: 'im-cut-review', shadow: 'none' }} />

<script lang="ts">
  import { tick } from 'svelte';
  type Shot = {
    asset_id: string; day: string; new_day: boolean; chapter: string;
    story_title: string; moment: string; reason: string; timecode: string;
    seconds: number; motion: boolean; included: boolean; kind_label: string;
    decision?: { model_reason: string; kept_reason: string; proposed_asset_id: string;
      offered_count: number; replacement_outcome: string };
  };
  type Model = { shots: Shot[]; editable: boolean; labels: Record<string, string> };
  let { payload = '{"shots":[],"editable":false,"labels":{}}' }: { payload?: string } = $props();
  const model: Model = $derived(JSON.parse(payload));
  const labels = $derived(model.labels);
  let selected = $state('');
  let filter = $state('all');
  let inspector = $state<HTMLElement>();
  let sheet: HTMLDivElement;
  const visible = $derived(model.shots.filter(s => filter === 'all' ||
    (filter === 'motion' && s.motion) || (filter === 'stills' && !s.motion) ||
    (filter === 'excluded' && !s.included)));
  const shot = $derived(visible.find(s => s.asset_id === selected) ?? visible[0]);
  const image = (id: string, preview = false) => `/media/thumb/${encodeURIComponent(id)}${preview ? '?size=preview' : ''}`;
  function act(action: string, asset_id: string, included?: boolean) {
    $host().dispatchEvent(new CustomEvent('review-action', { detail: { action, asset_id, included } }));
  }
  async function inspect(asset_id: string) {
    selected = asset_id;
    await tick();
    if (matchMedia('(max-width: 700px)').matches) inspector?.scrollIntoView({ block: 'start' });
  }
</script>

<div class="cut-review">
  <div class="review-toolbar">
    <p>{labels.savedTiming}</p>
    <label>{labels.show}
      <select bind:value={filter}>
        <option value="all">{labels.all}</option>
        <option value="motion">{labels.videos}</option>
        <option value="stills">{labels.stills}</option>
        <option value="excluded">{labels.excluded}</option>
      </select>
    </label>
  </div>
  <div class="review-layout">
    <div class="contact-sheet" aria-label={labels.contactSheet} bind:this={sheet}>
      {#each visible as item (item.asset_id)}
        {#if item.chapter}<h3 class="storyboard-chapter">{item.chapter}</h3>{/if}
        <button class="storyboard-shot" class:active={shot?.asset_id === item.asset_id}
          class:excluded={!item.included} aria-pressed={shot?.asset_id === item.asset_id}
          onclick={() => inspect(item.asset_id)}>
          <span class="shot-position"><span>{String(model.shots.indexOf(item) + 1).padStart(2, '0')}</span><span>{item.timecode}</span></span>
          <span class="shot-image"><img src={image(item.asset_id)} alt={item.moment || item.story_title} loading="lazy" /></span>
          <span class="shot-meta"><span class:storyboard-day={item.new_day}>{item.day}</span><span>{item.seconds.toFixed(1)} s</span></span>
          <span class="storyboard-story">{item.story_title}</span>
          <span class="shot-status">{item.kind_label}{!item.included ? ` / ${labels.excluded}` : ''}</span>
        </button>
      {:else}
        <p class="empty-review">{labels.noPictures}</p>
      {/each}
    </div>
    {#if shot}
      <aside class="picture-inspector" aria-label={labels.pictureReview} bind:this={inspector}>
        <img class="inspector-image" src={image(shot.asset_id, true)} alt={shot.moment || shot.story_title} />
        <div class="inspector-body">
          <div class="shot-meta"><span>{shot.day}</span><span>{shot.timecode} / {shot.seconds.toFixed(1)} s</span></div>
          <h3>{shot.story_title}</h3>
          <h4>{labels.why}</h4>
          <p>{shot.reason || labels.noReason}</p>
          {#if shot.decision?.model_reason}
            <h4>{labels.modelSuggestion}</h4><p>{shot.decision.model_reason}</p>
          {/if}
          {#if shot.decision?.kept_reason}
            <h4>{labels.keptBecause}</h4><p>{shot.decision.kept_reason}</p>
          {/if}
          {#if shot.decision && shot.decision.offered_count > 0}
            <p>{labels.alternativeCount}: {shot.decision.offered_count}</p>
          {/if}
          {#if shot.decision?.proposed_asset_id}
            <h4>{labels.alternative}</h4>
            <img class="alternative-image" src={image(shot.decision.proposed_asset_id)} alt={labels.alternative} loading="lazy" />
            <p>{shot.decision.replacement_outcome || labels.noOutcome}</p>
          {/if}
          <div class="review-controls">
            {#if model.editable}
              <label class="include-picture"><input type="checkbox" checked={shot.included}
                onchange={e => act('include', shot.asset_id, e.currentTarget.checked)} />{labels.include}</label>
              <p class="selection-note">{labels.selectionNote}</p>
              {#if shot.motion}<button onclick={() => act('trim', shot.asset_id)}>{labels.trim}</button>{/if}
            {/if}
            <button onclick={() => act('decisions', shot.asset_id)}>{labels.decisions}</button>
            <button onclick={() => act('pool', shot.asset_id)}>{labels.alternatives}</button>
            <button class="back-to-pictures" onclick={() => sheet.scrollIntoView({ block: 'start' })}>{labels.backToPictures}</button>
          </div>
        </div>
      </aside>
    {/if}
  </div>
</div>

<style>
  :global(.cut-views), :global(.cut-views > .q-panel) { overflow: visible; }
  .cut-review { color: var(--im-text); font: inherit; width: 100%; }
  .cut-review p, .cut-review h3, .cut-review h4 { margin: 0; }
  .review-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 16px; font-size: 12px; color: var(--im-text-secondary); }
  .review-toolbar label { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
  .cut-review select, .review-controls button { background: var(--im-bg-elevated); color: var(--im-text); border: 1px solid var(--im-border-hover); border-radius: 6px; padding: 8px 10px; font: inherit; cursor: pointer; }
  .review-layout { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 24px; align-items: start; }
  .contact-sheet { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 18px 16px; align-items: start; }
  .storyboard-chapter, .empty-review { grid-column: 1 / -1; font-size: 14px; font-weight: 600; }
  .storyboard-shot { min-width: 0; display: flex; flex-direction: column; gap: 6px; text-align: left; border: 2px solid transparent; border-radius: 8px; padding: 7px; background: transparent; color: inherit; font: inherit; cursor: pointer; }
  .storyboard-shot:hover { background: var(--im-bg-surface); }
  .storyboard-shot.active { border-color: var(--im-primary); }
  .shot-position, .shot-meta { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; font-size: 11px; font-variant-numeric: tabular-nums; color: var(--im-text-secondary); }
  .shot-position { font-weight: 600; }
  .shot-image { display: flex; width: 100%; aspect-ratio: 4 / 3; background: var(--im-bg-surface); border-radius: 4px; overflow: hidden; }
  .shot-image img { width: 100%; height: 100%; object-fit: contain; }
  .storyboard-story { font-size: 13px; font-weight: 600; line-height: 1.4; }
  .shot-status { font-size: 11px; color: var(--im-text-secondary); }
  .excluded .shot-image { opacity: .45; }
  .excluded .shot-status { color: var(--im-warning-text); }
  .picture-inspector { position: sticky; top: 24px; max-height: calc(100dvh - 48px); border: 1px solid var(--im-border); border-radius: 10px; overflow: auto; background: var(--im-bg-surface); }
  .inspector-image { display: block; width: 100%; aspect-ratio: 4 / 3; object-fit: contain; background: var(--im-bg); }
  .inspector-body { padding: 18px; }
  .inspector-body h3 { font-size: 17px; line-height: 1.4; font-weight: 600; margin: 10px 0 20px; }
  .inspector-body h4 { font-size: 12px; font-weight: 600; margin: 18px 0 6px; }
  .inspector-body p { font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
  .alternative-image { width: 100%; max-height: 140px; object-fit: contain; margin: 4px 0; }
  .review-controls { display: flex; flex-direction: column; gap: 10px; border-top: 1px solid var(--im-border); margin-top: 20px; padding-top: 18px; }
  .include-picture { display: flex; align-items: center; gap: 10px; font-size: 13px; font-weight: 600; cursor: pointer; }
  .include-picture input { width: 17px; height: 17px; accent-color: var(--im-primary); }
  .review-controls .selection-note { font-size: 11px; color: var(--im-text-secondary); }
  .review-controls button { text-align: left; font-size: 12px; }
  .back-to-pictures { display: none; }
  .cut-review :focus-visible { outline: 3px solid var(--im-primary); outline-offset: 3px; }
  @media (max-width: 1050px) { .review-layout { grid-template-columns: minmax(0, 1fr) 280px; gap: 16px; } .contact-sheet { grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; } }
  @media (max-width: 700px) { .review-layout { grid-template-columns: 1fr; } .picture-inspector { position: static; grid-row: 1; max-height: none; } .inspector-image { max-height: 220px; } .review-toolbar { align-items: flex-start; flex-direction: column; } .back-to-pictures { display: block; } }
</style>
