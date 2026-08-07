# JabRef Helper

A small Python script for cleaning up JabRef BibTeX files.

It:

- replaces citation keys such as `2024` with an author and year key
- changes `collaborator` to `author` when an author is missing
- removes entries with duplicate titles, keeping the first one
- renames linked PDF files when their citation key changes

## Usage

Python 3 is required.

```sh
python main.py references.bib
```

The original file is left unchanged. The cleaned file is saved beside it as
`references_fixed.bib`. If that name already exists, a number is added to the
new filename.

Linked PDFs are only renamed when they are in the same folder as the BibTeX
file.
