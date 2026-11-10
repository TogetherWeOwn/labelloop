# Detection fixtures

Two deliberately tiny domains validate that later compiler and training work is not tied to one subject domain. Each contains **16** letterboxed 160 × 160 JPEG images; these are bootstrap inputs, not a quality benchmark or completed detection-label release. The `objects` fields are coarse subject hints from the selection, not canonical boxes; M1 adds canonical labels. See [ADR 0002](../docs/adr/0002-m0-commons-fixture-source.md) for the fixture-source decision.

## `commons-office-keyboards`

16 non-game office keyboard photographs from Wikimedia Commons. Each image's linked Commons file page declares [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The manifest records the title, exact source page, original file, served thumbnail, creator, licence, and generated-image SHA-256 for every entry. Attribution is retained inside the repository; keep the manifest with any redistribution or derivative use.

The images include complete and laptop keyboards, exposed keyboard assemblies and individual switches. They do not represent one visual template or a completed labelled dataset.

## `cc0-interface`

16 non-game software-interface screenshots from Wikimedia Commons, all declared [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/deed.en) on the linked file page. The original URL, source page, title and creator are retained per image. These images contain ordinary graphical and web application interfaces, not game screenshots. Software or content appearing within an image may have its own licences, which the source page describes; only CC0-declared source records are selected.

- [Wikibase Registry screenshot example](https://commons.wikimedia.org/w/index.php?curid=92052635)
- [CC0 search used for source discovery](https://commons.wikimedia.org/w/index.php?search=filetype%3Abitmap+incategory%3ACC-Zero+screenshot&title=Special%3AMediaSearch&type=image)

## Reproduction and integrity

`manifest.json` contains the SHA-256 of every committed image and an exact `processing` record: Python 3.12, Pillow, the underlying libjpeg-turbo and JPEG compatibility versions, EXIF normalization and RGB conversion, Lanczos interpolation, the centered letterbox geometry and background, and JPEG quality/optimization/progressive settings. The generator reads these settings and rejects a runtime or manifest mismatch before downloading; the offline validator verifies this historical record unchanged, plus a separate current runtime pin (Pillow 13.0.0 and libjpeg-turbo 3.2.0). A provisioning codec change still fails explicitly rather than silently changing fixture bytes. The compiler fixture loader additionally compares normalized image hashes against the unchanged canonical goldens; compiler tests rebuild each release twice under the current runtime. See [ADR 0003](../docs/adr/0003-imaging-runtime-refresh.md). The generator is a provisioning utility, not part of the runtime. The keyboard generator re-reads each public Commons API record and rejects a changed licence, creator, or title before downloading. Rebuilding writes only to a new explicit output directory and fails rather than replacing committed records if an upstream derivative changes. Four interface originals (`ui-13.jpg`, `ui-14.jpg`, `ui-15.jpg`, and `ui-16.jpg`) have changed upstream; their committed bytes and attribution remain intact, but their current original URLs no longer reproduce the original hashes. The manifest marks these entries with `original_changed_upstream`. Regeneration of this domain therefore stops on that hash mismatch. It writes a temporary sibling directory and removes that incomplete domain on failure; the already completed keyboard domain remains, so a retry of the whole command needs a new top-level output argument. The validator checks the committed evidence offline, not those upstream derivatives.

```sh
uv run python -I fixtures/prepare.py /absolute/path/to/new-output-directory
uv run python -I scripts/check-fixtures.py /absolute/path/to/new-output-directory
```

The verification checks 32 images across two domains, exact dimensions, HTTPS `upload.wikimedia.org` source URLs and Commons source pages, title and creator fields, per-image licences and SHA-256, and total size below 5 MB. It validates the retained provenance records; this does not imply an independent copyright audit. Use the optional output-directory arguments to compare a regeneration without modifying committed fixtures.
