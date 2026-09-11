# JabRef Helper

A small Python script for cleaning up JabRef BibTeX files.

It:

- generates missing citation keys from the first author's surname and the
  publication year
- replaces citation keys such as `2024` with an author and year key
- changes `collaborator` to `author` when an author is missing
- merges entries with duplicate titles to preserve their fields, then removes
  the redundant entry
- renames linked PDF files when their citation key changes

## Usage

Python 3 is required.

```sh
python main.py references.bib
```

The original file is copied beside it as `references_old.bib`, and the cleaned
content replaces `references.bib`. If the backup name already exists, a number
is added (for example, `references_old_2.bib`) so no earlier backup is
overwritten.

Linked PDFs are only renamed when they are in the same folder as the BibTeX
file.
