
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
S = open(base+r'\SRD521.full.txt', encoding='utf-8').read().replace('\r\n','\n')
S = re.sub(r'-\n','',S); S = re.sub(r'\n+','\n',S)
for kw in ['Dual Wielder','Actor','War Caster','Speedy','Lucky','Mounted Combatant','Observant','Martial Weapon Training','Inspiring Leader','Boon of Recovery','Boon of Speed','Boon of Energy Resistance','Boon of Fortitude','Boon of Skill','Tavern Brawler','Dueling','Shadow-Touched','Fey-Touched','Light Armor Training','Medium Armor Training','Heavy Armor Training','Crusher','Great Weapon Master','Polearm Master','Medium Armor Master','Heavy Armor Master','Shield Master','Spell Sniper','Sharpshooter','Musician','Charger','Defensive Duelist','Keen Mind','Interception','Piercer','Skulker','Ritual Caster','Slasher','Crafter','Unarmed Fighting','Blind Fighting','Elemental Adept','Sentinel','Telekinetic','Mage Slayer','Chef','Crossbow Expert','Skill Expert','Greatsword','College of Valor','Boon of','Feat']:
    print(kw, S.count(kw))
print('==== Feats chapter content')
i = S.find('Origin Feats')
print(S[i-1200:i+200])

