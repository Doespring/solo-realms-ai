
import json, sqlite3
p = r"D:\placeholder"
p = __import__('sys').argv[1]
c = sqlite3.connect("file:"+p+"?mode=ro", uri=True)
for (name, sql) in c.execute("select name, sql from sqlite_master where type in ('table','view') order by name"):
    print("==", name)
    print("  ", (sql or '').replace(chr(10),' ')[:400])
    try:
        n = c.execute('select count(*) from "'+name+'"').fetchone()[0]
        print("   rows:", n)
    except Exception as e:
        print("   err:", e)
