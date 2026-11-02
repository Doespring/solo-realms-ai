
import sys, sqlite3, json
c = sqlite3.connect("file:"+sys.argv[1]+"?mode=ro", uri=True)
print("BOOKS:")
for r in c.execute("select title,edition,kind,license,partial,pages,readable,complete,index_version from books"):
    print("  ", r)
print("TOPICS (sections count per book,topic):")
for r in c.execute("select b.title, s.topic, count(*) from sections s join books b on b.sha=s.book group by b.title, s.topic order by b.title, 3 desc"):
    print("  ", r)
print("SAMPLE:")
for r in c.execute("select id,page,printed,heading,substr(text,1,160),public,lead from sections where public=1 limit 5"):
    print("  ", r)
