
s = b"import sys as _s\n_s.path.insert(0, 'C:/elsewhere')\n"
print('slice contains path.insert   :', b'path.insert' in s)
print('slice contains sys.path      :', b'sys.path' in s)
print('slice contains sys.path.ins  :', b'sys.path.ins' in s)
print('s[14:28]                     :', repr(s[14:28]))
t = "sys.path.insert"
print('str test                     :', t in s.decode())
