"""Build one offline HTML file, with local CSS, JS and original art embedded."""
import base64
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
def build(source=None,target=None):
    source=Path(source or ROOT/'site');target=Path(target or ROOT/'黑白测评组_双击打开.html')
    html=(source/'index.html').read_text(encoding='utf-8')
    data=(source/'data.js').read_text(encoding='utf-8')
    for path in (source/'assets').rglob('*'):
        if path.is_file():
            mime={'svg':'image/svg+xml','webp':'image/webp','png':'image/png','jpg':'image/jpeg'}.get(path.suffix[1:])
            if mime:data=data.replace(path.relative_to(source).as_posix(),'data:'+mime+';base64,'+base64.b64encode(path.read_bytes()).decode())
    css=(source/'style.css').read_text(encoding='utf-8')
    script=(source/'app.js').read_text(encoding='utf-8')
    # Embed only repository code and JSON that escapes HTML end tags.
    data=data.replace('</script','<\\/script')
    html=html.replace('<link rel="stylesheet" href="style.css">','<style>'+css+'</style>')
    html=html.replace('<script src="data.js"></script>','<script>'+data+'</script>')
    html=html.replace('<script src="app.js"></script>','<script>'+script+'</script>')
    target.write_text(html,encoding='utf-8');return target
if __name__=='__main__':print(build())
