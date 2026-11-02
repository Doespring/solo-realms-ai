
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
S = load('SRD521.full.txt'); G=load('GM2024ru.full.txt')
for kw in ['Armor Training','Armor Class','Proficiency Bonus','Hit Point Dice','Hit Points','Ability Check','Saving Throw','Attack Roll','Difficulty Class','Advantage','Disadvantage','Initiative','Bloodied','Weapon Mastery','Mastery Property','Condition','Damage Type','Creature Type','Alignment','Heroic Inspiration','Temporary Hit Points','Cover','Darkvision','Blindsight','Truesight','Telepathy','Passive Perception','Search action','Study action','Influence action','Utilize action','Hide action','Dodge action','Ready action','Disengage action','Dash action','Help action','Magic action','Incapacitated','Prone','Stunned','Restrained','Petrified','Exhaustion','Unconscious']:
    print(kw, S.count(kw))

