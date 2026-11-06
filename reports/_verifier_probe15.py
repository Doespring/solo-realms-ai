
import os, sys, json, tempfile
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from solo.contracts import PlayRequest, ActionRequest
print(PlayRequest.model_json_schema())
print(ActionRequest.model_json_schema())
