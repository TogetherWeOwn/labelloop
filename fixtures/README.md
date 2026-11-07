# Detection fixtures

Two deliberately tiny domains validate that later compiler and training work is not tied to one subject domain. Each contains **16** letterboxed 160 × 160 JPEG images; these are bootstrap inputs, not a quality benchmark or a completed detection-label release. Labels are supplied by the relevant source metadata; M1 adds the canonical boxes.

## `coco-office`

16 photographs containing office and desk objects (including keyboards, mice, laptops, monitors and books) from the COCO 2017 validation set. COCO annotations and website are licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), copyright COCO Consortium; the COCO project does **not** own the photographs. Each photograph retains the licence assigned by its Flickr creator. The `coco_id`, `licence_id` and original Flickr source URL are in `manifest.json` for every image, so attribution can be traced individually.

| COCO licence ID | Licence |
| --- | --- |
| 1, 2 | [CC BY-NC-SA 2.0](https://creativecommons.org/licenses/by-nc-sa/2.0/) |
| 3 | [CC BY-NC 2.0](https://creativecommons.org/licenses/by-nc/2.0/) |

This mixed open-source/not-for-commercial-use fixture is for research testing only; a commercial deployment must separately review the image rights or replace it. The selection excludes NC-incompatible licence ID 4 (CC BY-NC-ND, which does not permit derivatives such as resizing). Current entries use IDs 1–3 only. Source and terms:

- [COCO licence terms](https://cocodataset.org/#termsofuse)
- [COCO home](https://cocodataset.org/#home)

## `cc0-interface`

16 non-game software-interface screenshots from Wikimedia Commons, all declared [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/deed.en) on the linked file page. The original URL, source page, title and creator are retained per image. These images contain ordinary graphical and web application interfaces, not game screenshots. Software or content appearing within an image may have its own licenses, which the source page describes; only CC0-declared source records are selected.

- [Wikibase Registry screenshot example](https://commons.wikimedia.org/w/index.php?curid=92052635)
- [CC0 search used for source discovery](https://commons.wikimedia.org/w/index.php?search=filetype%3Abitmap+incategory%3ACC-Zero+screenshot&title=Special%3AMediaSearch&type=image)

## Reproduction and integrity

`manifest.json` contains the SHA-256 of every committed image. The generator resizes sources using Pillow's Lanczos filter into 160 × 160 RGB JPEGs (quality 65). It is a provisioning utility, not part of the runtime. Rebuilding requires the exact pinned COCO annotation archive and source thumbnails; it is not run by CI because the fixture bytes are committed.

```sh
python3 -I fixtures/prepare.py
python3 -I scripts/check-fixtures.py
```

The verification checks 32 images across two domains, the exact dimensions, source URLs and per-image licences, SHA-256, and total size below 5 MB.
