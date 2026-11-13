"""Accept one lexical JSON envelope, never repair data or execute code."""
import re
def unframe(raw):
    match=re.fullmatch(r'\s*```(?:json)?\s*\n([\s\S]*?)\n```\s*',raw,re.I)
    return (match.group(1),'json_fence') if match else (raw,None)
