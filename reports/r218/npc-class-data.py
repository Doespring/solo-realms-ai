"""How much of the book's own text can give an NPC a sheet (r218, for the report).

Reads every NPC of every module scene the library actually cut from a PDF and
asks the same function the join route asks: does the printed sentence about this
person name his class next to his own name?

Run from the engine tree:

    .venv/bin/python reports/r218/npc-class-data.py
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from solo.nat20_recruit import CLASS_SLUGS, read_hint


def scenes(path='runtime/web-local/adventure.sqlite3'):
    db = sqlite3.connect(path)
    people = {}
    for module, body in db.execute('select module, body from module_scenes'):
        for npc in (json.loads(body).get('npcs') or []):
            people.setdefault((npc.get('name'), module), npc)
    return people


def main():
    people = scenes()
    hits, misses = [], []
    for (name, module), npc in sorted(people.items(), key=lambda row: str(row[0][0])):
        text = (npc.get('quote') or '', npc.get('gloss') or '')
        hint = read_hint(name or '', *text)
        row = (name, hint['class_slug'], hint['species_slug'],
               (hint['evidence'] or '').replace('\ufffe', '').replace('\n', ' ')[:110])
        (hits if hint['from_book'] else misses).append(row)
    print('NPC в сценах книг:', len(people))
    print('класс читается из текста:', len(hits))
    print('класс не назван (минимальный лист):', len(misses))
    print('классы движка:', len(CLASS_SLUGS))
    for row in hits:
        print('  +', ' | '.join(str(part) for part in row))
    for row in misses[:5]:
        print('  -', row[0])


if __name__ == '__main__':
    main()
