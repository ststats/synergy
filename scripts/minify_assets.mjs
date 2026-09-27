// 배포용 docs/의 JS·CSS·HTML에서 주석과 공백을 뺀다(저장소의 원본 templates/는 그대로 - 읽고 고치기 쉽게).
// 사용: node scripts/minify_assets.mjs docs
//
// - JS(파일, HTML 안 <script>): terser로 다시 출력만 한다(compress·mangle 끔 - 변수 이름·코드 모양을 바꾸지 않아서
//   HTML의 onclick="함수()"·다른 파일의 전역 이름이 그대로 이어진다). 원본과 압축본을 acorn으로 파싱해 구문 트리가
//   같은지 확인하고, 다르면 그 조각은 원본을 그대로 둔다(압축 때문에 사이트가 깨지지 않게).
//   정규식 기반 도구(rjsmin 등)는 템플릿 문자열(`...`) 안의 공백까지 지워 화면 글자를 바꿔서 쓰지 않는다.
// - CSS(파일, HTML 안 <style>): esbuild로 공백·주석만 뺀다(값·선택자는 그대로).
// - HTML: <!-- --> 주석만 뺀다(<script>·<style>·<pre>·<textarea> 안은 건드리지 않는다).
// - *.min.js·*.min.css는 이미 압축돼 있어 건너뛴다. /*! … */·@license 주석은 남긴다.
import fs from 'node:fs';
import path from 'node:path';
import { minify } from 'terser';
import * as acorn from 'acorn';
import { transform } from 'esbuild';

const root = process.argv[2];
if (!root || !fs.existsSync(root)) {
    console.error('사용: node scripts/minify_assets.mjs <docs 폴더>');
    process.exit(1);
}

// 구문 트리 비교: 위치·원문(raw)과 의미가 같은 표기 차이({a} = {a: a}, "a": = a:, () => { return x; } = () => x)만 맞춘다
function normalize(n) {
    if (Array.isArray(n)) return n.map(normalize);
    if (!n || typeof n !== 'object') return n;
    if (n.type === 'ArrowFunctionExpression' && n.body && n.body.type === 'BlockStatement'
        && n.body.body.length === 1 && n.body.body[0].type === 'ReturnStatement' && n.body.body[0].argument) {
        n = { ...n, expression: true, body: n.body.body[0].argument };
    }
    if (n.type === 'Property' && !n.computed && n.key && n.key.type === 'Literal' && typeof n.key.value === 'string'
        && /^[A-Za-z_$][\w$]*$/.test(n.key.value)) {
        n = { ...n, key: { type: 'Identifier', name: n.key.value } };
    }
    const out = {};
    for (const k of Object.keys(n)) {
        if (!['start', 'end', 'loc', 'range', 'raw', 'shorthand'].includes(k)) out[k] = normalize(n[k]);
    }
    return out;
}
const tree = (code, sourceType) => JSON.stringify(normalize(acorn.parse(code, { ecmaVersion: 'latest', sourceType })));

const stats = { js: [0, 0], css: [0, 0], html: [0, 0], kept: [] };

async function minifyJs(code, label, sourceType = 'script') {
    try {
        const out = (await minify(code, {
            compress: false, mangle: false, module: sourceType === 'module',
            format: { comments: /^!|@license|@preserve/ },
        })).code;
        if (tree(code, sourceType) !== tree(out, sourceType)) throw new Error('구문 트리가 달라짐');
        return out;
    } catch (e) {
        stats.kept.push(`${label}: ${e.message}`);
        return code;
    }
}

async function minifyCss(code, label) {
    try {
        return (await transform(code, { loader: 'css', minifyWhitespace: true, legalComments: 'inline', charset: 'utf8' })).code.trim();
    } catch (e) {
        stats.kept.push(`${label}: ${e.message}`);
        return code;
    }
}

// HTML 안 <script>(src 없는 JS)·<style>을 압축하고, 그 밖의 <!-- --> 주석을 뺀다
async function minifyHtml(html, label) {
    const parts = html.split(/(<script\b[^>]*>[\s\S]*?<\/script>|<style\b[^>]*>[\s\S]*?<\/style>|<pre\b[\s\S]*?<\/pre>|<textarea\b[\s\S]*?<\/textarea>)/i);
    const out = [];
    for (const part of parts) {
        const script = part.match(/^(<script\b([^>]*)>)([\s\S]*?)(<\/script>)$/i);
        const style = part.match(/^(<style\b[^>]*>)([\s\S]*?)(<\/style>)$/i);
        if (script) {
            const attrs = script[2];
            const type = (attrs.match(/\btype\s*=\s*["']?([^"'\s>]+)/i) || [])[1] || '';
            const isJs = !/\bsrc\s*=/i.test(attrs) && (!type || /javascript|module/i.test(type)) && script[3].trim();
            out.push(isJs
                ? script[1] + await minifyJs(script[3], `${label} <script>`, /module/i.test(type) ? 'module' : 'script') + script[4]
                : part);
        } else if (style) {
            out.push(style[1] + await minifyCss(style[2], `${label} <style>`) + style[3]);
        } else if (/^<(pre|textarea)\b/i.test(part)) {
            out.push(part);
        } else {
            out.push(part.replace(/<!--(?!\[if)[\s\S]*?-->/g, ''));
        }
    }
    return out.join('');
}

function* walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, entry.name);
        if (entry.isDirectory()) yield* walk(p);
        else yield p;
    }
}

for (const file of walk(root)) {
    const name = path.basename(file);
    const kind = /\.min\.(js|css)$/.test(name) ? null
        : name.endsWith('.js') ? 'js' : name.endsWith('.css') ? 'css' : name.endsWith('.html') ? 'html' : null;
    if (!kind) continue;
    const src = fs.readFileSync(file, 'utf8');
    const label = path.relative(root, file);
    const out = kind === 'js' ? await minifyJs(src, label) : kind === 'css' ? await minifyCss(src, label) : await minifyHtml(src, label);
    stats[kind][0] += Buffer.byteLength(src);
    stats[kind][1] += Buffer.byteLength(out);
    if (out !== src) fs.writeFileSync(file, out);
}

const kb = n => `${Math.round(n / 1024)}KB`;
for (const k of ['js', 'css', 'html']) console.log(`✅ ${k}: ${kb(stats[k][0])} → ${kb(stats[k][1])}`);
if (stats.kept.length) {
    console.log(`⚠️ 압축하지 않고 원본 그대로 둔 조각 ${stats.kept.length}개:`);
    stats.kept.forEach(x => console.log('   ' + x));
}
