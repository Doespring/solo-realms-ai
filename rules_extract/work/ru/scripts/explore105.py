
import sys, re, json
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
S = load('SRD521.full.txt')
names = ['d20 Test','Heroic Inspiration','Proficiency Bonus','Tool Proficiency','Armor Training','Ability Check','Saving Throw','Attack Roll','Difficulty Class','Advantage','Disadvantage','Initiative','Bloodied','Weapon Mastery','Condition','Damage Type','Creature Type','Alignment','Temporary Hit Points','Hit Points','Hit Point Dice','Armor Class','Cover','Darkvision','Blindsight','Truesight','Telepathy','Passive Perception','Passive Perception Score','Speed','Difficult Terrain','Lightly Obscured','Heavily Obscured','Unarmed Strike','Opportunity Attack','Bonus Action','Reaction','Concentration','Cantrip','Spell Slot','Ritual','School of Magic','Area of Effect','Emanation','Cone','Cylinder','Line','Cube','Sphere','Stabilized','Death Saving Throw','Critical Hit','Long Rest','Short Rest','Attitude','Hostile','Friendly','Indifferent','Hazard','Burning','Dehydration','Malnutrition','Suffocation','Falling','Light Armor','Medium Armor','Heavy Armor','Shield','Simple Weapon','Martial Weapon','Melee Weapon','Ranged Weapon','Ammunition','Finesse','Loading','Range','Reach','Thrown','Two-Handed','Versatile','Cleave','Graze','Nick','Push','Sap','Slow','Topple','Vex','Bludgeoning','Piercing','Slashing','Acid','Cold','Fire','Force','Lightning','Necrotic','Poison','Psychic','Radiant','Thunder','Abjuration','Conjuration','Divination','Enchantment','Evocation','Illusion','Necromancy','Transmutation','Aberration','Beast','Celestial','Construct','Dragon','Elemental','Fey','Fiend','Giant','Humanoid','Monstrosity','Ooze','Plant','Undead','Tiny','Small','Medium','Large','Huge','Gargantuan']
out={}
for n in names: out[n]=S.count(n)
print(json.dumps(out, ensure_ascii=False, indent=0))

