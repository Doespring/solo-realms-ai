
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
S = load('SRD521.full.txt'); G = load('GM2024ru.full.txt'); P = load('PHB2024ru.full.txt')
w = ['Club','Dagger','Greatclub','Handaxe','Javelin','Light Hammer','Mace','Quarterstaff','Sickle','Spear','Dart','Light Crossbow','Shortbow','Sling','Battleaxe','Flail','Glaive','Greataxe','Greatsword','Halberd','Lance','Longsword','Maul','Morningstar','Pike','Rapier','Scimitar','Shortsword','Trident','Warhammer','War Pick','Whip','Blowgun','Hand Crossbow','Heavy Crossbow','Longbow','Musket','Pistol']
print('SRD missing weapons:', [x for x in w if x not in S])
wprop = ['Ammunition','Finesse','Heavy','Light','Loading','Range','Reach','Thrown','Two-Handed','Versatile']
print('SRD missing props:', [x for x in wprop if x not in S])
mastery = ['Cleave','Graze','Nick','Push','Sap','Slow','Topple','Vex']
print('SRD mastery:', {x: x in S for x in mastery})
i=S.find('Weapon Mastery')
print(S[i-500:i+1500])

