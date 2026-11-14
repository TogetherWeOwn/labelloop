# Voice and copy: tool surfaces

How labelloop's screens talk. Moved unchanged from [TogetherWeOwn/two-design-kit](https://github.com/TogetherWeOwn/two-design-kit) **v0.3.1** (`4eb4181`), `docs/voice.md` §7 "Tool surfaces (workbench)", including the term table.

Tool screens keep the Ember Hours mechanics (kit v0.3.1 `docs/voice.md` §2): sentence case, verbs first, digits, the middle dot, no exclamation marks, errors that say what to do. They swap the community voice for a colleague's: plain, exact, and honest about what a machine did.

1. **Say who did it.** A person by name ("Mora"), a model by its role and version ("the detector, yolo26n · r0007", "LLM review"). Never the passive voice for a decision ("was approved").
2. **Machines propose; people review.** Only a person "reviews", "accepts" or "adjudicates". A model "proposes", "suggests" or "auto-accepts under policy P-2". Never "verified", "approved" or "confirmed" for model output.
3. **Model text is quoted, never adopted.** Show a model's reason in a quote with "Model's reason · not checked by a person". Don't paraphrase it into our voice.
4. **Numbers with their basis.** "mAP50-95 0.634 on test set t-2 (1,114 frames)", "Estimate $1.38 · about 2 h at $0.69/h". An estimate always says "estimate"; a finite test never becomes "perfect".
5. **Money is exact.** "$1.38", two decimals, currency first; the button names the estimate ("Start · estimate $1.38"). Never call an estimate a cap: "up to", "at most" and "capped at" are only for an amount the server enforces.

| Use | Not |
|---|---|
| label (a box and its class) · box · object | annotation (in UI copy), bbox, instance |
| class | category, tag, label (for the class itself) |
| Proposed | pending, suggested label, prediction (as a state) |
| Auto-accepted | verified, approved, trusted |
| Reviewed · Adjudicated | confirmed, validated, QA'd |
| Uncertain | unknown (as a state), flagged |
| the detector · LLM review | the AI, the bot, the reviewer (for a model) |
| release (a frozen dataset) | version, snapshot, export |
| recipe | preset, transform, config |
| run | job, experiment (in UI copy) |
| current model · candidate | incumbent, champion, challenger (in UI copy; fine in docs) |
| Promote · Roll back | deploy, revert |
| key fingerprint | key, secret (when showing one) |
| Spend without asking | autonomous spend (in UI copy) |

