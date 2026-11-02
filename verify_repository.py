"""Check repository integrity, syntax and absence of admission-file formats.
Text fingerprints outside reanalysis_2026 normalize CRLF to LF; the frozen
reanalysis bundle is always verified byte for byte. No third-party dependencies.
"""
from pathlib import Path
import hashlib, json, ast, csv, zipfile
R = Path(__file__).resolve().parent
TEXT = {'.py', '.md', '.txt', '.json', '.csv', '.cff', '.yml', '.yaml'}
def normalized(path, data):
    if path.startswith('reanalysis_2026/'):
        return data
    return data.replace(b'\r\n', b'\n') if Path(path).suffix in TEXT or Path(path).name in {'.gitignore', '.gitattributes'} else data
def main():
    count = 0
    for line in (R / 'checksums/SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        expected, name = line.split('  ', 1)
        file = R / name
        assert file.is_file(), name
        assert hashlib.sha256(normalized(name, file.read_bytes())).hexdigest() == expected, name
        count += 1
    with (R/'FILE_INVENTORY.csv').open(encoding='utf-8', newline='') as stream:
        inventory = list(csv.DictReader(stream))
    for row in inventory:
        data = normalized(row['path'], (R/row['path']).read_bytes())
        assert len(data) == int(row['bytes']), row['path']
        assert hashlib.sha256(data).hexdigest() == row['sha256'], row['path']
    bundle = R / 'reanalysis_2026'
    manifest = json.loads((bundle/'package_manifest.json').read_text(encoding='utf-8'))
    for name, expected in manifest.items():
        assert hashlib.sha256((bundle/name).read_bytes()).hexdigest() == expected, name
    py_count = 0
    for file in R.rglob('*'):
        if not file.is_file() or '.git' in file.relative_to(R).parts or '__pycache__' in file.parts:
            continue
        assert file.suffix.lower() not in {'.parquet', '.feather', '.dbf', '.dbc', '.sav', '.dta', '.pkl', '.pickle', '.rds', '.rdata'}, str(file)
        if file.suffix == '.py':
            ast.parse(file.read_text(encoding='utf-8-sig')); py_count += 1
        if file.suffix == '.csv':
            with file.open(encoding='utf-8-sig', newline='') as stream:
                fields = set(next(csv.reader(stream), []))
            assert not {'age','hospital_id','death_in_hospital'} <= fields, str(file)
            assert not {'record_id','age'} <= fields, str(file)
    print(f'PASS: {count} repository fingerprints; {len(inventory)} inventory rows; '
          f'{len(manifest)} frozen bundle hashes; {py_count} Python files parsed.')
if __name__ == '__main__':
    main()
