# Public corpus local landing pattern

Use when asked to "采集/下载某某合集到本地" and the material is a public web/GitHub/forum/archive corpus rather than a single article.

## Goal

Produce a verifiable local corpus folder plus a compact landing note (e.g. an
Obsidian note, or just a plain `INDEX.md`). Do not mirror private, paid,
login-gated, or permission-restricted assets unless the user explicitly
provides authorization and the workflow remains within access boundaries.

## Steps

1. Discover public sources
   - Prefer Brave search when available; if it fails, use a fallback search path.
   - Prioritize sources with directly downloadable files or public Git repos over posts that only point to cloud-drive sales pages.
   - Record source URL and commit/hash if the source is a repo.

2. Save locally
   - Use a dated run folder under your local raw-data path, e.g. `<data-root>/<corpus>/<YYYYMMDD-HHMM>/`.
   - Clone public repos with `--depth 1` when history is not needed.
   - Keep acquisition logs (`clone.log`, download logs, etc.).

3. Build manifests
   - Create `manifest.csv` and, when useful, `manifest.json` with at least: relative path, filename, suffix, bytes, sha256, source path, duplicate marker.
   - Write `_summary.md` with source URLs, local paths, counts, and boundary note.
   - Write `_缺失与异常.md` for unavailable cloud-drive/full versions, failed searches, non-public resources, or intentionally skipped gated assets.

4. Extract readable text when the corpus contains PDFs/DOCX
   - For PDFs, `pdftotext -layout -enc UTF-8 input.pdf output.txt` is a reliable batch extractor when a Python PDF library is unavailable or unnecessary.
   - For DOCX, read `word/document.xml` from the zip and strip XML tags for a rough text extraction.
   - Save extracted text in `extracted-text/` and create `text-extraction-manifest.csv` with ok/chars/error fields.

5. Land in Obsidian
   - Do not paste all raw text into Obsidian.
   - Create a corpus folder under an appropriate Obsidian-indexed source-material area.
   - Minimum notes: `INDEX.md`, `_采集说明.md`, and category/主题 notes when the corpus is large.
   - `INDEX.md` should include: local raw path, document count, extracted text count/size, theme distribution, representative items, writing-material angles, and full file index.

## Verification checklist

- Raw folder exists.
- File count from filesystem matches manifest count.
- `manifest.csv` and `_summary.md` exist.
- If text extraction ran: extracted text count matches document count or failures are listed.
- Obsidian folder exists and contains `INDEX.md` plus `_采集说明.md`.
- Final reply returns paths and counts, not a long pasted artifact.
