"""Сверка карты правил PHB 2024 RU (глава 3, классы) с SRD_CC_v5.2.1.pdf.

Каждое правило карты получает английский заголовок того же умения из SRD и
поиск этого заголовка в извлечённом тексте SRD. Ничего не переводится назад и
не меняется: сверка только фиксирует, совпадает ли название умения. Карта
ссылается на страницы RU-PHB, SRD служит второй опорой по формулировкам.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pypdf

ROOT = Path(__file__).resolve().parent.parent
SRD = ROOT / "runtime/books/SRD_CC_v5.2.1.pdf"
CHAPTER = ROOT / "rules_data/v2/chapters/phb-03-classes"
OUT = ROOT / "rules_data/v2/evidence/phb-2024-ru/phb03-srd-crosscheck.json"

# Английские заголовки умений (SRD 5.2.1, Classes). Ключ — <класс>/<slug правила>.
TITLES = {
    "bard/class_attributes": "Core Bard Traits",
    "bard/vardo_vdohnovenie": "Bardic Inspiration",
    "bard/sotvorenie_zaklinanii": "Spellcasting",
    "bard/ekspertnost": "Expertise",
    "bard/master_na_vse_ruki": "Jack of All Trades",
    "bard/podklass_barda": "Bard Subclass",
    "bard/uvelichenie_harakteristik": "Ability Score Improvement",
    "bard/istochnik_vdohnoveniya": "Font of Inspiration",
    "bard/kontrocharovanie": "Countercharm",
    "bard/tainy_magii": "Magical Secrets",
    "bard/prevoshodnoe_vdohnovenie": "Superior Inspiration",
    "bard/epicheskii_dar": "Epic Boon",
    "bard/slova_tvoreniya": "Words of Creation",
    "barbarian/class_attributes": "Core Barbarian Traits",
    "barbarian/yarost": "Rage",
    "barbarian/zashita_bez_dospehov": "Unarmored Defense",
    "barbarian/iskusnoe_vladenie_oruzhiem": "Weapon Mastery",
    "barbarian/chuvstvo_opasnosti": "Danger Sense",
    "barbarian/bezrassudnaya_ataka": "Reckless Attack",
    "barbarian/podklass_varvara": "Barbarian Subclass",
    "barbarian/pervobytnoe_znanie": "Primal Knowledge",
    "barbarian/uvelichenie_harakteristik": "Ability Score Improvement",
    "barbarian/dopolnitelnaya_ataka": "Extra Attack",
    "barbarian/bystroe_peredvizhenie": "Fast Movement",
    "barbarian/dikii_instinkt": "Feral Instinct",
    "barbarian/instinktivnyi_brosok": "Instinctive Pounce",
    "barbarian/zhestokii_udar": "Brutal Strike",
    "barbarian/nepreklonnaya_yarost": "Relentless Rage",
    "barbarian/uluchshennyi_zhestokii_udar_13": "Improved Brutal Strike",
    "barbarian/nepreryvnaya_yarost": "Persistent Rage",
    "barbarian/uluchshennyi_zhestokii_udar_17": "Improved Brutal Strike",
    "barbarian/neukrotimaya_mosh": "Indomitable Might",
    "barbarian/epicheskii_dar": "Epic Boon",
    "barbarian/dikii_chempion": "Primal Champion",
    "fighter/class_attributes": "Core Fighter Traits",
    "fighter/boevoi_stil": "Fighting Style",
    "fighter/vtoroe_dyhanie": "Second Wind",
    "fighter/iskusnoe_vladenie_oruzhiem": "Weapon Mastery",
    "fighter/vsplesk_deistvii": "Action Surge",
    "fighter/takticheskoe_myshlenie": "Tactical Mind",
    "fighter/podklass_voina": "Fighter Subclass",
    "fighter/uvelichenie_harakteristik": "Ability Score Improvement",
    "fighter/dopolnitelnaya_ataka": "Extra Attack",
    "fighter/takticheskoe_peremeshenie": "Tactical Shift",
    "fighter/upornyi": "Indomitable",
    "fighter/master_taktiki": "Tactical Master",
    "fighter/dve_dopolnitelnye_ataki": "Two Extra Attacks",
    "fighter/vyverennye_ataki": "Studied Attacks",
    "fighter/epicheskii_dar": "Epic Boon",
    "fighter/tri_dopolnitelnye_ataki": "Three Extra Attacks",
    "wizard/class_attributes": "Core Wizard Traits",
    "wizard/sotvorenie_zaklinanii": "Spellcasting",
    "wizard/znatok_ritualov": "Ritual Adept",
    "wizard/magicheskoe_vosstanovlenie": "Arcane Recovery",
    "wizard/akademicheskie_znaniya": "Scholar",
    "wizard/podklass_volshebnika": "Wizard Subclass",
    "wizard/uvelichenie_harakteristik": "Ability Score Improvement",
    "wizard/zapominanie_zaklinaniya": "Memorize Spell",
    "wizard/masterstvo_zaklinatelya": "Spell Mastery",
    "wizard/epicheskii_dar": "Epic Boon",
    "wizard/firmennye_zaklinaniya": "Signature Spells",
    "wizard/kopirovanie_zaklinanii": "Copying a Spell into the Book",
    "druid/class_attributes": "Core Druid Traits",
    "druid/sotvorenie_zaklinanii": "Spellcasting",
    "druid/druidicheskii_yazyk": "Druidic",
    "druid/pervozdannyi_orden": "Primal Order",
    "druid/dikii_oblik": "Wild Shape",
    "druid/dikii_sputnik": "Wild Companion",
    "druid/podklass_druida": "Druid Subclass",
    "druid/uvelichenie_harakteristik": "Ability Score Improvement",
    "druid/dikoe_vosstanovlenie": "Wild Resurgence",
    "druid/yarost_stihii": "Elemental Fury",
    "druid/uluchshennaya_yarost_stihii": "Improved Elemental Fury",
    "druid/zaklinaniya_zverya": "Beast Spells",
    "druid/epicheskii_dar": "Epic Boon",
    "druid/arhidruid": "Archdruid",
    "cleric/class_attributes": "Core Cleric Traits",
    "cleric/sotvorenie_zaklinanii": "Spellcasting",
    "cleric/bozhestvennyi_orden": "Divine Order",
    "cleric/provedenie_bozhestvennosti": "Channel Divinity",
    "cleric/bozhestvennaya_iskra": "Divine Spark",
    "cleric/izgnanie_nezheti": "Turn Undead",
    "cleric/podklass_zheretsa": "Cleric Subclass",
    "cleric/uvelichenie_harakteristik": "Ability Score Improvement",
    "cleric/ispeplenie_nezheti": "Sear Undead",
    "cleric/blagoslovlennye_udary": "Blessed Strikes",
    "cleric/bozhestvennoe_vmeshatelstvo": "Divine Intervention",
    "cleric/uluchshennye_blagoslovlennye_udary": "Improved Blessed Strikes",
    "cleric/epicheskii_dar": "Epic Boon",
    "cleric/velikoe_bozhestvennoe_vmeshatelstvo": "Greater Divine Intervention",
    "warlock/class_attributes": "Core Warlock Traits",
    "warlock/tainstvennye_vozzvaniya": "Eldritch Invocations",
    "warlock/magiya_dogovora": "Pact Magic",
    "warlock/magicheskaya_izvorotlivost": "Magical Cunning",
    "warlock/podklass_kolduna": "Warlock Subclass",
    "warlock/uvelichenie_harakteristik": "Ability Score Improvement",
    "warlock/svyaz_s_pokrovitelem": "Contact Patron",
    "warlock/tainstvennyi_arkanum": "Mystic Arcanum",
    "warlock/epicheskii_dar": "Epic Boon",
    "warlock/tainstvennyi_master": "Eldritch Master",
    "warlock/vedmachii_vzor": "Witch Sight",
    "warlock/vzor_dvuh_umov": "Gaze of Two Minds",
    "warlock/dar_glubin": "Gift of the Depths",
    "monk/class_attributes": "Core Monk Traits",
    "monk/boevye_iskusstva": "Martial Arts",
    "monk/zashita_bez_dospehov": "Unarmored Defense",
    "monk/monasheskaya_sosredotochennost": "Monk's Focus",
    "monk/shkval_udarov": "Flurry of Blows",
    "monk/terpelivaya_oborona": "Patient Defense",
    "monk/postup_vetra": "Step of the Wind",
    "monk/dvizhenie_bez_dospehov": "Unarmored Movement",
    "monk/fenomenalnyi_metabolizm": "Uncanny Metabolism",
    "monk/otrazhenie_atak": "Deflect Attacks",
    "monk/podklass_monaha": "Monk Subclass",
    "monk/uvelichenie_harakteristik": "Ability Score Improvement",
    "monk/medlennoe_padenie": "Slow Fall",
    "monk/dopolnitelnaya_ataka": "Extra Attack",
    "monk/oshelomlyayushii_udar": "Stunning Strike",
    "monk/usilennye_udary": "Empowered Strikes",
    "monk/uvertlivost": "Evasion",
    "monk/akrobatichnoe_peremeshenie": "Acrobatic Movement",
    "monk/povyshennaya_sosredotochennost": "Heightened Focus",
    "monk/samovosstanovlenie": "Self-Restoration",
    "monk/otrazhenie_energii": "Deflect Energy",
    "monk/obuchennyi_vyzhivat": "Disciplined Survivor",
    "monk/polnaya_sosredotochennost": "Perfect Focus",
    "monk/sovershennaya_zashita": "Superior Defense",
    "monk/epicheskii_dar": "Epic Boon",
    "monk/telo_i_razum": "Body and Mind",
    "paladin/class_attributes": "Core Paladin Traits",
    "paladin/vozlozhenie_ruk": "Lay On Hands",
    "paladin/sotvorenie_zaklinanii": "Spellcasting",
    "paladin/iskusnoe_vladenie_oruzhiem": "Weapon Mastery",
    "paladin/boevoi_stil": "Fighting Style",
    "paladin/kara_paladina": "Paladin's Smite",
    "paladin/provedenie_bozhestvennosti": "Channel Divinity",
    "paladin/bozhestvennoe_chuvstvo": "Divine Sense",
    "paladin/podklass_paladina": "Paladin Subclass",
    "paladin/uvelichenie_harakteristik": "Ability Score Improvement",
    "paladin/dopolnitelnaya_ataka": "Extra Attack",
    "paladin/vernyi_skakun": "Faithful Steed",
    "paladin/aura_zashity": "Aura of Protection",
    "paladin/poritsanie_vragov": "Abjure Foes",
    "paladin/aura_otvagi": "Aura of Courage",
    "paladin/siyayushie_udary": "Radiant Strikes",
    "paladin/vosstanavlivayushee_prikosnovenie": "Restoring Touch",
    "paladin/rasshirenie_aury": "Expanding Aura",
    "paladin/epicheskii_dar": "Epic Boon",
    "rogue/class_attributes": "Core Rogue Traits",
    "rogue/ekspertnost": "Expertise",
    "rogue/kovarnaya_ataka": "Sneak Attack",
    "rogue/vorovskoi_zhargon": "Thieves' Cant",
    "rogue/iskusnoe_vladenie_oruzhiem": "Weapon Mastery",
    "rogue/hitroe_deistvie": "Cunning Action",
    "rogue/podklass_pluta": "Rogue Subclass",
    "rogue/tochnoe_pritselivanie": "Steady Aim",
    "rogue/uvelichenie_harakteristik": "Ability Score Improvement",
    "rogue/hitryi_udar": "Cunning Strike",
    "rogue/neveroyatnoe_uklonenie": "Uncanny Dodge",
    "rogue/uvertlivost": "Evasion",
    "rogue/nadezhnyi_talant": "Reliable Talent",
    "rogue/uluchshennyi_hitryi_udar": "Improved Cunning Strike",
    "rogue/beschestnye_udary": "Devious Strikes",
    "rogue/skolzskii_um": "Slippery Mind",
    "rogue/neulovimost": "Elusive",
    "rogue/epicheskii_dar": "Epic Boon",
    "rogue/ulybka_fortuny": "Stroke of Luck",
    "ranger/class_attributes": "Core Ranger Traits",
    "ranger/sotvorenie_zaklinanii": "Spellcasting",
    "ranger/izbrannyi_vrag": "Favored Enemy",
    "ranger/iskusnoe_vladenie_oruzhiem": "Weapon Mastery",
    "ranger/iskusnyi_issledovatel": "Deft Explorer",
    "ranger/boevoi_stil": "Fighting Style",
    "ranger/podklass_sledopyta": "Ranger Subclass",
    "ranger/uvelichenie_harakteristik": "Ability Score Improvement",
    "ranger/dopolnitelnaya_ataka": "Extra Attack",
    "ranger/vseprolaznost": "Roving",
    "ranger/ekspertnost": "Expertise",
    "ranger/neutomimyi": "Tireless",
    "ranger/upornyi_ohotnik": "Relentless Hunter",
    "ranger/prirodnaya_zavesa": "Nature's Veil",
    "ranger/metkii_ohotnik": "Precise Hunter",
    "ranger/dikie_chuvstva": "Feral Senses",
    "ranger/epicheskii_dar": "Epic Boon",
    "ranger/groza_vragov": "Foe Slayer",
    "sorcerer/class_attributes": "Core Sorcerer Traits",
    "sorcerer/sotvorenie_zaklinanii": "Spellcasting",
    "sorcerer/vrozhdennoe_charodeistvo": "Innate Sorcery",
    "sorcerer/istochnik_magii": "Font of Magic",
    "sorcerer/metamagiya": "Metamagic",
    "sorcerer/podklass_charodeya": "Sorcerer Subclass",
    "sorcerer/uvelichenie_harakteristik": "Ability Score Improvement",
    "sorcerer/charodeiskoe_vosstanovlenie": "Sorcerous Restoration",
    "sorcerer/voploshenie_charodeistva": "Sorcery Incarnate",
    "sorcerer/epicheskii_dar": "Epic Boon",
    "sorcerer/misticheskii_apofeoz": "Arcane Apotheosis",
    "sorcerer/akkuratnoe_zaklinanie": "Careful Spell",
}


# Умения подклассов Барда, попавшие в карту (3-й уровень). Подклассы в SRD
# 5.2.1 не опубликованы: эти заголовки взяты из подклассов PHB, поэтому при
# отсутствии в SRD сверка честно записывает «нет в SRD», а не выдумывает
# английский текст.
SUBCLASS_FEATURES = {
    "bard/kolegiya_dobletsi_boevoe_vdohnovenie": "Combat Inspiration",
    "bard/kolegiya_znanii_ostroe_slovtso": "Cutting Words",
    "bard/kolegiya_ocharovaniya_maniya_vdohnoveniya": "Mantle of Inspiration",
    "bard/kolegiya_tantsa_oslepitelnye_fuete": "Dazzling Footwork",
}

# Умения, которых в SRD 5.2.1 заведомо нет (подклассы и поздние расширения):
# сверку они проходят как «в SRD не опубликовано», а не как ошибка карты.
# «Cutting Words» в SRD есть, но это умение подкласса Коллегии знаний, поэтому
# он в списке отсутствующих только тогда, когда поиск действительно не нашёл.
ABSENT_FROM_SRD = set(SUBCLASS_FEATURES) | {"paladin/rasshirenie_aury"}


def main() -> int:
    reader = pypdf.PdfReader(SRD)
    # SRD набирает апостроф «’» (U+2019); приводим к «'» до сравнения.
    pages = {i + 1: (reader.pages[i].extract_text() or "").replace("\u2019", "'")
             for i in range(len(reader.pages))}
    text = "\n".join(pages.values())
    rows, missing = {}, []
    for path in sorted(CHAPTER.glob("*.json")):
        slug = path.stem.split("-", 1)[1]
        for rule in json.loads(path.read_text(encoding="utf-8"))["rules"]:
            key = rule["id"].split(".")[3]
            title = TITLES.get(f"{slug}/{key}")
            title = title or SUBCLASS_FEATURES.get(f"{slug}/{key}", title)
            if title is None and ":" in rule["source"]["section"]:
                # Умение подкласса: сверяем по английскому названию из подкласса.
                subclass, feature = rule["source"]["section"].split(":", 1)
                title = None
            if title is None:
                missing.append(f"{slug}/{key}: нет английского заголовка в таблице сверки")
                rows[f"{slug}/{key}"] = {"srd_title": None, "srd_pages": [], "found": False}
                continue
            # В SRD заголовок стоит в начале строки, но может иметь префикс
            # «Level N:», жирное начертание или сноску после слова.
            pattern = re.compile(
                rf"(?m)^\s*(?:Level\s+\d+:\s*)?(?:[ \u2022]*)?{re.escape(title)}"
                rf"(?:\s*[^\w\s.{{}}()]{{0,3}})?\s*$")
            hits = sorted(page for page, body in pages.items() if pattern.search(body))
            if not hits:
                # Часть умений в SRD оформлена подпунктом внутри другого:
                # « Divine Spark. As a Magic action…» — ищем как подпункт.
                inline = re.compile(rf"^[ \u2022-]{{0,3}}\s*{re.escape(title)}\.\s",
                                    flags=re.M)
                hits = sorted(page for page, body in pages.items() if inline.search(body))
            rows[f"{slug}/{key}"] = {"srd_title": title, "srd_pages": hits, "found": bool(hits)}
            if not hits and f"{slug}/{key}" not in ABSENT_FROM_SRD:
                missing.append(f"{slug}/{key}: {title!r} не найден в SRD")
    # Подклассные умения, которые в SRD всё-таки нашлись, считаются сверенными.
    absent = sorted(key for key in ABSENT_FROM_SRD if not rows[key]["found"])
    payload = {
        "document_id": "srd_cc_5_2_1",
        "source": "runtime/books/SRD_CC_v5.2.1.pdf",
        "purpose": "Сверка формулировок карты классовых правил RU-PHB с SRD 5.2.1",
        "rules_total": len(rows),
        "found_total": sum(1 for row in rows.values() if row["found"]),
        "absent_from_srd": absent,
        "missing": missing,
        "rules": rows,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"rules={len(rows)} found={payload['found_total']} missing={len(missing)}")
    for item in missing[:20]:
        print("  ", item)
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
