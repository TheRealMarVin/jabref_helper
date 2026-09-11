#!/usr/bin/env python3
"""
Fix JabRef keys such as 2024 or 2024b.

Version 2.4: merges duplicate entries before removing the redundant copies.

Usage:
    python fix_jabref_bib.py references.bib

The original .bib file is copied to references_old.bib, and the cleaned data
is written back to references.bib.
Linked PDFs are renamed only when they are directly beside the .bib file.
"""

import argparse
import re
import shutil
import sys
import unicodedata
from pathlib import Path


YEAR_KEY = re.compile(r"^\d{4}[a-z]*$", re.IGNORECASE)


def find_entries(text):
    """Return normal BibTeX entries while respecting nested braces."""
    entries = []
    position = 0

    while True:
        match = re.search(r"@([A-Za-z]+)\s*\{", text[position:])
        if match is None:
            return entries

        start = position + match.start()
        entry_type = match.group(1).lower()
        opening = position + match.end() - 1
        depth = 0
        escaped = False

        for index in range(opening, len(text)):
            character = text[index]

            if escaped:
                escaped = False
                continue

            if character == "\\":
                escaped = True
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        else:
            raise ValueError("Unclosed BibTeX entry near character " + str(start))

        position = end

        if entry_type in {"comment", "preamble", "string"}:
            continue

        entry_text = text[start:end]
        comma = entry_text.find(",", opening - start + 1)
        if comma == -1:
            raise ValueError("Could not read a citation key near " + str(start))

        key_start = opening - start + 1
        raw_key = entry_text[key_start:comma]
        left = len(raw_key) - len(raw_key.lstrip())
        right = len(raw_key) - len(raw_key.rstrip())

        entries.append(
            {
                "start": start,
                "end": end,
                "text": entry_text,
                "original_text": entry_text,
                "key": raw_key.strip(),
                "key_start": key_start + left,
                "key_end": comma - right,
            }
        )


def find_field(entry_text, name):
    """Return a field value and its position inside one entry."""
    match = re.search(
        r"(?im)^[ \t]*" + re.escape(name) + r"[ \t]*=[ \t]*",
        entry_text,
    )
    if match is None:
        return None

    start = match.end()
    while start < len(entry_text) and entry_text[start].isspace():
        start += 1

    if start >= len(entry_text):
        return None

    if entry_text[start] == "{":
        depth = 0
        escaped = False

        for index in range(start, len(entry_text)):
            character = entry_text[index]

            if escaped:
                escaped = False
                continue

            if character == "\\":
                escaped = True
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    return {
                        "value": entry_text[start + 1:index],
                        "start": start + 1,
                        "end": index,
                    }

        raise ValueError("Unclosed field: " + name)

    if entry_text[start] == '"':
        escaped = False

        for index in range(start + 1, len(entry_text)):
            character = entry_text[index]

            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                return {
                    "value": entry_text[start + 1:index],
                    "start": start + 1,
                    "end": index,
                }

        raise ValueError("Unclosed field: " + name)

    end = entry_text.find(",", start)
    if end == -1:
        end = len(entry_text)

    value = entry_text[start:end].strip()
    value_start = entry_text.find(value, start, end)
    return {
        "value": value,
        "start": value_start,
        "end": value_start + len(value),
    }


def clean_key_part(value):
    """Turn a surname into a lowercase ASCII BibTeX key component."""
    value = re.sub(
        r"""\\(?:['"`^~=.uvHckbrd])\s*\{?([A-Za-z])\}?""",
        r"\1",
        value,
    )
    value = re.sub(r"\\[A-Za-z]+\s*\{([^{}]*)\}", r"\1", value)
    value = value.replace("{", "").replace("}", "").replace("\\", "")
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "", value).lower()


def first_author_surname(author_value):
    first_author = re.split(
        r"\s+and\s+",
        author_value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()

    while first_author.startswith("{") and first_author.endswith("}"):
        first_author = first_author[1:-1].strip()

    if not first_author or first_author.lower() == "others":
        return ""

    if "," in first_author:
        surname = first_author.split(",", 1)[0]
    else:
        words = first_author.split()
        if not words:
            return ""

        surname_words = [words[-1]]
        index = len(words) - 2

        # Keep particles such as de, van, von, and der.
        while index >= 0 and words[index][:1].islower():
            surname_words.insert(0, words[index])
            index -= 1

        surname = " ".join(surname_words)

    return clean_key_part(surname)


def suffix(number):
    result = ""
    number += 1

    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(ord("a") + remainder) + result

    return result


def unique_key(base_key, used_keys):
    candidate = base_key
    number = 0

    while candidate.lower() in used_keys:
        candidate = base_key + suffix(number)
        number += 1

    used_keys.add(candidate.lower())
    return candidate


def promote_collaborator_to_author(entry_text):
    """Rename collaborator to author while keeping the equals sign aligned."""
    pattern = re.compile(
        r"(?im)^([ \t]*)collaborator([ \t]*)=([ \t]*)"
    )

    def replace_field(match):
        indent = match.group(1)
        spacing_before_equals = match.group(2)
        spacing_after_equals = match.group(3)

        # Keep the equals sign in its original column when possible.
        original_width = len("collaborator") + len(spacing_before_equals)
        new_spacing_width = max(1, original_width - len("author"))
        return (
            indent
            + "author"
            + (" " * new_spacing_width)
            + "="
            + spacing_after_equals
        )

    return pattern.sub(replace_field, entry_text, count=1)


def normalized_title(value):
    """Normalize a title for duplicate comparison."""
    # BibTeX braces are commonly used only to preserve capitalization, so
    # they should not make otherwise identical titles appear different.
    value = value.replace("{", "").replace("}", "")
    return " ".join(value.split()).casefold()


def find_fields(entry_text):
    """Return all fields in an entry, including their value positions."""
    opening = entry_text.find("{")
    if opening == -1:
        return []

    key_comma = entry_text.find(",", opening + 1)
    if key_comma == -1:
        return []

    fields = []
    position = key_comma + 1
    closing = len(entry_text) - 1

    while position < closing:
        while position < closing and (
            entry_text[position].isspace() or entry_text[position] == ","
        ):
            position += 1

        if position >= closing:
            break

        name_match = re.match(r"[A-Za-z][A-Za-z0-9_:-]*", entry_text[position:])
        if name_match is None:
            # Be tolerant of comments or syntax that this small parser does
            # not understand; continue at the next line.
            newline = entry_text.find("\n", position)
            if newline == -1:
                break
            position = newline + 1
            continue

        name_start = position
        name = name_match.group(0)
        position += len(name)
        while position < closing and entry_text[position].isspace():
            position += 1
        if position >= closing or entry_text[position] != "=":
            continue

        position += 1
        while position < closing and entry_text[position].isspace():
            position += 1
        if position >= closing:
            break

        delimiter = entry_text[position]
        if delimiter == "{":
            depth = 1
            escaped = False
            value_start = position + 1
            position += 1
            while position < closing and depth:
                character = entry_text[position]
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == "{":
                    depth += 1
                elif character == "}":
                    depth -= 1
                position += 1
            if depth:
                raise ValueError("Unclosed field: " + name)
            value_end = position - 1
        elif delimiter == '"':
            escaped = False
            value_start = position + 1
            position += 1
            while position < closing:
                character = entry_text[position]
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    break
                position += 1
            if position >= closing:
                raise ValueError("Unclosed field: " + name)
            value_end = position
            position += 1
        else:
            value_start = position
            depth = 0
            while position < closing:
                character = entry_text[position]
                if character == "{":
                    depth += 1
                elif character == "}" and depth:
                    depth -= 1
                elif character == "," and depth == 0:
                    break
                position += 1
            value_end = position
            while value_end > value_start and entry_text[value_end - 1].isspace():
                value_end -= 1

        line_start = entry_text.rfind("\n", 0, name_start) + 1
        indent = entry_text[line_start:name_start]
        if not indent.isspace():
            indent = "  "
        fields.append(
            {
                "name": name,
                "value": entry_text[value_start:value_end],
                "start": value_start,
                "end": value_end,
                "indent": indent,
            }
        )

    return fields


def information_score(value):
    """Estimate how much useful information a conflicting value contains."""
    plain = re.sub(r"[{}\\\s]", "", value)
    return (len(re.findall(r"[A-Za-z0-9]", plain)), len(value))


def merge_list_values(first, second, separator):
    """Combine delimited values without repeating identical items."""
    items = []
    seen = set()
    for value in (first, second):
        for item in value.split(separator):
            item = item.strip()
            normalized = " ".join(item.split()).casefold()
            if item and normalized not in seen:
                seen.add(normalized)
                items.append(item)
    return (separator + " ").join(items)


def add_field(entry_text, name, value):
    """Add a braced field just before an entry's closing brace."""
    closing = entry_text.rfind("}")
    if closing == -1:
        raise ValueError("Could not find the end of a BibTeX entry")

    fields = find_fields(entry_text)
    indent = fields[-1]["indent"] if fields else "  "
    before = entry_text[:closing].rstrip()
    if not before.endswith(","):
        before += ","
    return before + "\n" + indent + name + " = {" + value + "},\n" + entry_text[closing:]


def merge_entries(retained_text, duplicate_text):
    """Merge duplicate fields into the retained entry."""
    added = []
    enriched = []

    for incoming in find_fields(duplicate_text):
        fields_by_name = {
            field["name"].casefold(): field
            for field in find_fields(retained_text)
        }
        name = incoming["name"].casefold()
        current = fields_by_name.get(name)

        if current is None:
            retained_text = add_field(
                retained_text,
                incoming["name"],
                incoming["value"],
            )
            added.append(incoming["name"])
            continue

        old_value = current["value"]
        new_value = incoming["value"]
        if " ".join(old_value.split()).casefold() == " ".join(new_value.split()).casefold():
            continue

        if name == "file":
            merged_value = merge_list_values(old_value, new_value, ";")
        elif name in {"keywords", "groups"}:
            merged_value = merge_list_values(old_value, new_value, ",")
        elif information_score(new_value) > information_score(old_value):
            merged_value = new_value
        else:
            merged_value = old_value

        if merged_value != old_value:
            retained_text = (
                retained_text[:current["start"]]
                + merged_value
                + retained_text[current["end"]:]
            )
            enriched.append(incoming["name"])

    return retained_text, added, enriched


def find_duplicate_titles(entries):
    """Merge equal-title entries, keeping the first and removing the rest."""
    seen_titles = {}
    retained = []
    duplicates = []

    for entry in entries:
        title = find_field(entry["text"], "title")
        if title is None or not normalized_title(title["value"]):
            retained.append(entry)
            continue

        normalized = normalized_title(title["value"])
        if normalized in seen_titles:
            retained_entry = seen_titles[normalized]
            merged_text, added, enriched = merge_entries(
                retained_entry["text"],
                entry["text"],
            )
            retained_entry["text"] = merged_text
            duplicates.append(
                {
                    "entry": entry,
                    "title": " ".join(title["value"].split()),
                    "kept_key": retained_entry["key"],
                    "added_fields": added,
                    "enriched_fields": enriched,
                }
            )
        else:
            seen_titles[normalized] = entry
            retained.append(entry)

    return retained, duplicates


def plan_changes(entries):
    # Preserve all keys that are already meaningful.
    used_keys = {
        entry["key"].lower()
        for entry in entries
        if not YEAR_KEY.fullmatch(entry["key"])
    }

    changes = []
    warnings = []

    for entry in entries:
        old_key = entry["key"]
        author = find_field(entry["text"], "author")
        collaborator = find_field(entry["text"], "collaborator")
        promote_collaborator = author is None and collaborator is not None

        # Use collaborator as the author source when JabRef/arXiv placed the
        # names in that field. The output entry will rename it to author.
        author_source = author if author is not None else collaborator
        new_key = old_key

        if YEAR_KEY.fullmatch(old_key):
            year = find_field(entry["text"], "year")

            if author_source is None:
                warnings.append(
                    old_key
                    + ": missing author and collaborator; entry left unchanged."
                )
                used_keys.add(old_key.lower())
            else:
                surname = first_author_surname(author_source["value"])
                if not surname:
                    warnings.append(
                        old_key
                        + ": could not determine the surname; citation key left unchanged."
                    )
                    used_keys.add(old_key.lower())
                else:
                    year_match = None
                    if year is not None:
                        year_match = re.search(r"\b(\d{4})\b", year["value"])
                    if year_match is None:
                        year_match = re.search(r"\b(\d{4})\b", old_key)

                    new_key = unique_key(
                        surname + year_match.group(1),
                        used_keys,
                    )

        if promote_collaborator or new_key != old_key:
            changes.append(
                {
                    "entry": entry,
                    "new_key": new_key,
                    "promote_collaborator": promote_collaborator,
                }
            )

    return changes, warnings


def available_filename(folder, filename, source):
    target = folder / filename
    if not target.exists() or target == source:
        return target

    number = 2
    while True:
        candidate = folder / (
            target.stem + " (" + str(number) + ")" + target.suffix
        )
        if not candidate.exists():
            return candidate
        number += 1


def rename_linked_pdfs(entry_text, old_key, new_key, folder):
    file_field = find_field(entry_text, "file")
    if file_field is None:
        return entry_text, [], []

    renamed = []
    warnings = []
    updated_attachments = []

    for attachment in file_field["value"].split(";"):
        parts = attachment.split(":", 2)

        if len(parts) < 3:
            updated_attachments.append(attachment)
            continue

        description, stored_name, remainder = parts
        filename = stored_name.replace(r"\:", ":")

        # Only rename PDFs directly beside the .bib file.
        if (
            not filename.lower().endswith(".pdf")
            or "/" in filename
            or "\\" in filename
        ):
            updated_attachments.append(attachment)
            continue

        source = folder / filename
        if not source.is_file():
            warnings.append(
                old_key + ": PDF not found in the same folder: " + filename
            )
            updated_attachments.append(attachment)
            continue

        stem = source.stem
        match = re.match(
            r"^\s*"
            + re.escape(old_key)
            + r"(?:\s*[-_]\s*|\s+)(.+)$",
            stem,
            re.IGNORECASE,
        )
        title_part = match.group(1).strip() if match else stem.strip()
        wanted_name = new_key + " - " + title_part + source.suffix
        target = available_filename(folder, wanted_name, source)

        if target != source:
            source.rename(target)

        updated_name = target.name.replace(":", r"\:")
        updated_attachments.append(
            description + ":" + updated_name + ":" + remainder
        )
        renamed.append(source.name + " -> " + target.name)

    new_value = ";".join(updated_attachments)
    new_entry = (
        entry_text[:file_field["start"]]
        + new_value
        + entry_text[file_field["end"]:]
    )
    return new_entry, renamed, warnings


def choose_backup_path(input_path):
    backup = input_path.with_name(
        input_path.stem + "_old" + input_path.suffix
    )
    number = 2

    while backup.exists():
        backup = input_path.with_name(
            input_path.stem
            + "_old_"
            + str(number)
            + input_path.suffix
        )
        number += 1

    return backup


def main():
    version = "2.4"
    parser = argparse.ArgumentParser()
    parser.add_argument("bib_file", help="BibTeX file to process")
    arguments = parser.parse_args()

    input_path = Path(arguments.bib_file).expanduser().resolve()

    if not input_path.is_file():
        print("File not found: " + str(input_path), file=sys.stderr)
        return 1

    try:
        data = input_path.read_bytes()
        encoding = "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
        text = data.decode(encoding)

        entries = find_entries(text)
        retained_entries, duplicate_titles = find_duplicate_titles(entries)
        changes, warnings = plan_changes(retained_entries)
        replacements = []
        renamed_files = []

        # Preserve the exact input file before changing it or linked PDFs.
        backup_path = choose_backup_path(input_path)
        shutil.copy2(input_path, backup_path)

        for duplicate in duplicate_titles:
            entry = duplicate["entry"]
            replacements.append((entry["start"], entry["end"], ""))

        updated_entries = {
            entry["start"]: entry["text"] for entry in retained_entries
        }

        for change in changes:
            entry = change["entry"]
            new_key = change["new_key"]
            new_entry = updated_entries[entry["start"]]

            if change["promote_collaborator"]:
                new_entry = promote_collaborator_to_author(new_entry)

            renamed = []
            pdf_warnings = []
            if new_key != entry["key"]:
                new_entry, renamed, pdf_warnings = rename_linked_pdfs(
                    new_entry,
                    entry["key"],
                    new_key,
                    input_path.parent,
                )

                # The citation key appears before all fields, so renaming a
                # later collaborator field does not change these offsets.
                new_entry = (
                    new_entry[:entry["key_start"]]
                    + new_key
                    + new_entry[entry["key_end"]:]
                )

            updated_entries[entry["start"]] = new_entry
            renamed_files.extend(renamed)
            warnings.extend(pdf_warnings)

        for entry in retained_entries:
            new_entry = updated_entries[entry["start"]]
            if new_entry != entry["original_text"]:
                replacements.append((entry["start"], entry["end"], new_entry))

        for start, end, replacement in sorted(
            replacements,
            key=lambda item: item[0],
            reverse=True,
        ):
            text = text[:start] + replacement + text[end:]

        input_path.write_text(text, encoding=encoding, newline="")
    except (OSError, UnicodeError, ValueError) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1

    print("fix_jabref_bib version " + version)
    print("Backup: " + str(backup_path))
    print("Updated: " + str(input_path))
    key_changes = [
        change
        for change in changes
        if change["new_key"] != change["entry"]["key"]
    ]
    collaborator_changes = [
        change for change in changes if change["promote_collaborator"]
    ]

    print("Citation keys changed: " + str(len(key_changes)))
    print("Duplicate-title entries removed: " + str(len(duplicate_titles)))
    print(
        "Collaborator fields changed to author: "
        + str(len(collaborator_changes))
    )
    print("PDF files renamed: " + str(len(renamed_files)))

    for change in key_changes:
        print(
            "  "
            + change["entry"]["key"]
            + " -> "
            + change["new_key"]
        )

    for change in collaborator_changes:
        print(
            "  Field: "
            + change["entry"]["key"]
            + ": collaborator -> author"
        )

    for duplicate in duplicate_titles:
        merge_details = []
        if duplicate["added_fields"]:
            merge_details.append(
                "added " + ", ".join(duplicate["added_fields"])
            )
        if duplicate["enriched_fields"]:
            merge_details.append(
                "enriched " + ", ".join(duplicate["enriched_fields"])
            )
        details = "; " + "; ".join(merge_details) if merge_details else ""
        print(
            "  Duplicate title: "
            + duplicate["entry"]["key"]
            + " merged into and removed; kept "
            + duplicate["kept_key"]
            + " ("
            + duplicate["title"]
            + details
            + ")"
        )

    for renamed in renamed_files:
        print("  PDF: " + renamed)

    for warning in warnings:
        print("Warning: " + warning, file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
