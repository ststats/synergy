// 시너지 페이지 스크립트(모든 페이지 공용). CSP가 인라인 스크립트를 막아 파일로 둔다.
// 페이지별 값은 HTML의 <script type="application/json" id="page-config">에서 읽는다.
(async function () {
    const PAGE = JSON.parse(document.getElementById('page-config').textContent);
    const TEAM_COLORS = PAGE.colors;
    const TARGET_TEAM = PAGE.teamFromUrl
        ? (new URLSearchParams(window.location.search).get('team') || '').trim()
        : PAGE.targetTeam;
    const LOGO_PREFIX = PAGE.logoPrefix;
    const IS_PROFILE = PAGE.isProfile;
    // 이미지를 못 불러오면 data-fallback대로 가린다(hidden: 자리는 두고 숨김, none: 자리까지 없앰).
    // 이 파일보다 먼저 실패한 이미지는 아래에서 마저 처리한다.
    const hideBrokenImage = img => {
        img.style[img.dataset.fallback === 'none' ? 'display' : 'visibility'] = img.dataset.fallback;
    };
    document.addEventListener(
        'error',
        e => {
            if (e.target instanceof HTMLImageElement && e.target.dataset.fallback) hideBrokenImage(e.target);
        },
        true
    );
    // src를 아직 넣지 않은 이미지(개인 페이지 사진)는 실패가 아니다
    document.querySelectorAll('img[data-fallback][src]').forEach(img => {
        if (img.complete && !img.naturalWidth) hideBrokenImage(img);
    });
    const PROFILE_ID = IS_PROFILE
        ? (new URLSearchParams(window.location.search).get('id') || '').trim().toLowerCase()
        : '';
    // 프로필의 뒤로가기 목적지. referrer는 새 탭·프라이버시 설정에 따라 오지 않을 수 있어 URL 값을 쓴다.
    const FROM_TEAM = new URLSearchParams(window.location.search).get('fromTeam');

    // 수장·전력외는 순위·평균·상위 % 계산에서 뺀다(줄은 빨간 표시로 남긴다)
    const isExcludedRole = m => m.role === '수장' || m.role === '전력외';
    const formatCount = v => (v ? v.toLocaleString('ko-KR') : '');
    // unit: 표 열 머리 글자. count: 순위 패널 값 뒤 세는 단위(값에 이미 단위가 보이면 비움)
    // source: 출처(SOURCES). 월 누적 지표는 트래키파이
    const metricDefs = {
        balloon: {
            field: 'balloons',
            label: '별풍선',
            unit: '별풍선',
            count: '개',
            format: formatCount,
            source: 'trackify',
        },
        broadcast: {
            field: 'broadcast_seconds',
            label: '방송시간',
            unit: '방송시간',
            count: '',
            format: formatTime,
            source: 'trackify',
        },
        viewer: {
            field: 'cumulative_viewers',
            label: '누적시청자',
            unit: '누적시청자',
            count: '명',
            format: formatCount,
            source: 'trackify',
        },
        // 뷰어십 = 시청자 수 × 방송 시간. 초로 오는 값을 시간으로 보인다.
        // 값이 있는 날짜에서만 고를 수 있다(syncViewershipMetric)
        viewership: {
            field: 'viewership_seconds',
            label: '뷰어십',
            unit: '뷰어십(시간)',
            count: '시간',
            format: v => formatCount(Math.round((v || 0) / 3600)),
            source: 'trackify',
        },
        sponsor: {
            field: 'sponsor_games',
            label: '스폰판수',
            unit: '스폰판수',
            count: '',
            format: v => (v ? v + '판' : ''),
            source: 'elo',
        },
    };
    const SOURCES = {
        trackify: { name: 'Trackify', url: 'https://www.trackify.kr' },
        elo: { name: 'Elo', url: 'https://eloboard.co.kr/' },
    };

    // SOOP 프로필 사진의 작은 판(약 66px WebP, 스타유니브 core.js getProfileImgUrl과 같은 주소).
    // 화면에는 22~24px로만 써서 큰 원본 JPG 대신 받는다.
    function soopPhotoUrl(id) {
        const safe = encodeURIComponent(
            String(id || '')
                .trim()
                .toLowerCase()
        );
        return `https://stimg.sooplive.com/LOGO/${safe.substring(0, 2)}/${safe}/m/${safe}.webp`;
    }

    // URL 파라미터를 속성에 넣을 때: &·= 주입을 막으려 encodeURIComponent 후 escapeHtml한다.
    function attrUrlParam(value) {
        return escapeHtml(encodeURIComponent(value == null ? '' : value));
    }

    // 상속 프로퍼티(constructor 등)가 값처럼 잡히지 않게 한다(?metric=constructor로 빈 화면이 되는 문제).
    function ownGet(obj, key) {
        if (!obj || key == null) return undefined;
        return Object.prototype.hasOwnProperty.call(obj, key) ? obj[key] : undefined;
    }

    // CSS 컨텍스트에는 escapeHtml로 부족해 #rrggbb 형태만 통과시킨다(아니면 기본 색).
    // 색이 정해지지 않은 대학의 기본 색. 강조색과 겹치지 않게 무채색
    const DEFAULT_TEAM_COLOR = 'var(--c-team-default)';
    function safeCssColor(value) {
        return /^#[0-9a-fA-F]{3,8}$/.test(String(value || '')) ? String(value) : DEFAULT_TEAM_COLOR;
    }

    // 로스터에서 온 외부 입력 문자열은 innerHTML에 넣기 전에 반드시 이 함수를 거친다(XSS 방어).
    function escapeHtml(s) {
        return String(s ?? '').replace(
            /[&<>"']/g,
            c =>
                ({
                    '&': '&amp;',
                    '<': '&lt;',
                    '>': '&gt;',
                    '"': '&quot;',
                    "'": '&#39;',
                })[c]
        );
    }

    // 방송 중 여부(player_live). 시청자 수는 최대 2분 전 값이다. 방송 종료만 null이며 조회 실패는 예외다.
    async function checkIsLiveRealtime(soopId) {
        const client = synergySupabaseClient();
        if (!client || !soopId) throw new Error('방송 상태를 조회할 수 없습니다');
        // 휴면 선수도 보이도록 한 사람용 함수로 읽는다
        const { data, error } = await client.rpc('player_live', { p_soop_id: soopId });
        if (error || !Array.isArray(data)) throw error || new Error('Invalid live status');
        const row = data[0];
        if (!row || !row.broad_no) return null;
        return { broad: row, broadStart: row.broad_start || null };
    }

    function fitTextToWidth(el, fullText) {
        if (!el) return;
        fullText = fullText || '';
        el.dataset.fullText = fullText;
        el.textContent = fullText;
        if (!fullText || el.scrollWidth <= el.clientWidth) return;
        // 한 글자씩 줄이면 긴 제목에서 리플로우가 수백 번 나서 이분 탐색으로 길이를 찾는다.
        let lo = 0,
            hi = fullText.length,
            best = 0;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            el.textContent = fullText.slice(0, mid) + '…';
            if (el.scrollWidth <= el.clientWidth) {
                best = mid;
                lo = mid + 1;
            } else {
                hi = mid - 1;
            }
        }
        el.textContent = fullText.slice(0, Math.max(1, best)) + '…';
    }

    function refitLiveTexts() {
        document.querySelectorAll('#profile-live-embed [data-full-text]').forEach(el => {
            fitTextToWidth(el, el.dataset.fullText);
        });
    }

    let liveFitResizeTimer = null;
    window.addEventListener('resize', () => {
        clearTimeout(liveFitResizeTimer);
        liveFitResizeTimer = setTimeout(refitLiveTexts, 150);
    });

    // 상단바: 한 줄 배치로 넘치는지 재 보고 넘치면 .is-stacked로 바꾼다. 폭·글자가 바뀌면 다시 잰다.
    // 검색칸이 90px 미만으로 좁아지면 안내 글자를 비워 돋보기만 남긴다.
    // (CSS 컨테이너 쿼리는 배치 바꾸기와 겹치면 크롬이 검색칸을 0 높이로 그려 쓰지 않는다)
    const topBar = document.querySelector('.top-bar');
    const searchInput = document.getElementById('player-search');
    const pickerGroup = document.querySelector('.month-select-group');
    function fitTopBar() {
        if (searchInput) searchInput.parentElement.style.setProperty('--search-w', pickerGroup.offsetWidth + 'px');
        topBar.classList.remove('is-stacked');
        topBar.classList.toggle('is-stacked', topBar.scrollWidth > topBar.clientWidth);
        if (searchInput)
            searchInput.placeholder = searchInput.offsetWidth < 90 ? '' : searchInput.getAttribute('aria-label');
    }
    if (topBar) {
        let topBarWidth = 0;
        new ResizeObserver(([entry]) => {
            if (entry.contentRect.width === topBarWidth) return;
            topBarWidth = entry.contentRect.width;
            fitTopBar();
        }).observe(topBar);
        const textWatch = new MutationObserver(fitTopBar);
        for (const el of topBar.querySelectorAll('#calendar-btn, #metric-btn, #top-meta-text'))
            textWatch.observe(el, { childList: true, subtree: true, characterData: true });
        if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitTopBar);
    }

    // 방송 중 목록(api_live_ids)은 ststat live-status가 2분마다 채운다. 같은 간격으로 보이는 페이지에서만 갱신한다.
    const LIVE_REFRESH_MS = 2 * 60 * 1000;
    let liveLastRun = 0;
    let liveTimer = null;
    let livePending = false;
    let liveIdsCache = null;
    let refreshProfileLive = null;

    function applyLiveDots(live) {
        document.querySelectorAll('.live-dot[data-live-id]').forEach(el => {
            el.classList.toggle('is-live', live.has(el.dataset.liveId.toLowerCase()));
        });
    }

    function scheduleLiveStatus() {
        clearTimeout(liveTimer);
        if (!IS_PROFILE && liveIdsCache) applyLiveDots(liveIdsCache);
        if (document.hidden || livePending) return;
        const wait = Math.max(0, LIVE_REFRESH_MS - (Date.now() - liveLastRun));
        liveTimer = setTimeout(refreshLiveStatus, wait);
    }

    async function refreshLiveStatus() {
        if (document.hidden || livePending) return;
        livePending = true;
        liveLastRun = Date.now();
        const host = document.getElementById(IS_PROFILE ? 'profile-live-embed' : 'grid-container');
        try {
            if (IS_PROFILE) await refreshProfileLive?.();
            else await refreshLiveDots();
            if (host) {
                host.dataset.liveState = 'ready';
                host.removeAttribute('title');
            }
        } catch (_) {
            if (host) {
                host.dataset.liveState = 'error';
                host.title = '방송 상태를 확인하지 못했습니다. 이전 표시가 남아 있을 수 있습니다.';
            }
        } finally {
            livePending = false;
            scheduleLiveStatus();
        }
    }

    // 방송 중인 SOOP ID 목록(소문자 Set). 미리 받아 둔 것(liveIdsPrefetch)은 처음 한 번만 쓴다.
    let liveIdsPrefetch = null;
    async function fetchLiveIds() {
        const client = synergySupabaseClient();
        if (!client) throw new Error('Supabase browser client is not configured');
        const { data, error } = await client.rpc('api_live_ids');
        if (error || !Array.isArray(data)) throw error || new Error('Invalid live status');
        return new Set(data.filter(r => r && r.soop_id).map(r => String(r.soop_id).toLowerCase()));
    }

    async function refreshLiveDots() {
        const prefetched = liveIdsPrefetch;
        liveIdsPrefetch = null;
        // 생방송 점은 부가 정보라 실패해도 본문에 영향이 없어야 한다.
        liveIdsCache = await (prefetched || fetchLiveIds());
        if (!document.hidden) applyLiveDots(liveIdsCache);
    }

    function formatTime(sec) {
        if (!sec) return '';
        let h = Math.floor(sec / 3600);
        let m = Math.floor((sec % 3600) / 60);
        let s = Math.floor(sec % 60);
        return (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
    }

    // timestamptz는 UTC로 오므로 KST로 바꿔 보인다.
    function formatKstTimestamp(value) {
        if (!value) return '';
        const parsed = new Date(value);
        if (!Number.isFinite(parsed.getTime())) return String(value).replace('T', ' ').slice(2, 16);
        const parts = new Intl.DateTimeFormat('en-CA', {
            timeZone: 'Asia/Seoul',
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            hourCycle: 'h23',
        })
            .formatToParts(parsed)
            .reduce((out, part) => {
                if (part.type !== 'literal') out[part.type] = part.value;
                return out;
            }, {});
        // 'YY-MM-DD HH:MM'(KST, 'KST' 글자는 붙이지 않는다)
        return `${parts.year.slice(-2)}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
    }

    // 공개 읽기 함수(api_*·player_*)만 GET으로 부르므로 supabase-js(213KB) 없이 fetch로 충분하다.
    const REQUEST_TIMEOUT_MS = 8000;
    function synergySupabaseClient() {
        const cfg = window.SYNERGY_SUPABASE_CONFIG;
        if (!cfg || !cfg.url || !cfg.key) return null;
        // 응답이 멈추면 본문 받기까지 포함해 8초에 끊는다. 재시도 1번까지 20초 안에 안내 화면으로 넘어간다.
        const get = (path, params) => {
            const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
            const timer = ctrl ? setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS) : null;
            return fetch(`${cfg.url}/rest/v1/${path}?${params}`, {
                headers: { apikey: cfg.key, Authorization: `Bearer ${cfg.key}` },
                signal: ctrl ? ctrl.signal : undefined,
            })
                .then(async res => {
                    const body = await res.json().catch(() => null);
                    if (ctrl && ctrl.signal.aborted) throw new Error('요청 시간 초과');
                    return res.ok
                        ? { data: body, error: null }
                        : { data: null, error: new Error((body && body.message) || `HTTP ${res.status}`) };
                })
                .catch(err => ({ data: null, error: err }))
                .finally(() => {
                    if (timer) clearTimeout(timer);
                });
        };
        return {
            // STABLE 함수는 GET으로 부른다. null·빈 문자열 인자는 보내지 않는다(기본값 사용).
            rpc(fn, args) {
                const params = new URLSearchParams();
                Object.entries(args || {}).forEach(([k, v]) => {
                    if (v !== null && v !== undefined && v !== '') params.set(k, v);
                });
                return get(`rpc/${fn}`, params);
            },
        };
    }

    async function loadAvailableDates() {
        const client = synergySupabaseClient();
        if (!client) throw new Error('Supabase browser client is not configured');
        const { data, error } = await client.rpc('api_stats_dates');
        if (error) throw error;
        return (Array.isArray(data) ? data : []).map(r => String(r.stat_date || '')).filter(Boolean);
    }

    // 목록 화면은 화면에 쓰는 칸만 받는다(생일은 birth_month만). 개인 페이지는 player_profile_stats로 전체 칸을 받는다.
    // light: 지난달 순위 증감 계산용 최소 칸. rankOnlyData에 따로 둔다.
    // latest: 날짜를 비워 가장 최근 날짜를 받는다(날짜 목록을 기다리지 않으려고). dateStr은 받은 행의 날짜가 된다.
    async function loadDailyData(dateStr, { light = false, latest = false } = {}) {
        const client = synergySupabaseClient();
        if (!client) throw new Error('Supabase browser client is not configured');

        let data = [];
        // 팀 페이지도 상위 1·5·10%를 전체 선수 기준으로 매기므로 전체를 받는다.
        if (IS_PROFILE) {
            const { data: rows, error } = await client.rpc('player_profile_stats', {
                p_soop_id: PROFILE_ID,
                p_date: latest ? null : dateStr,
            });
            if (error) throw error;
            if (Array.isArray(rows)) data.push(...rows);
        } else {
            const { data: rows, error } = await client.rpc('api_daily_stats', {
                p_date: latest ? null : dateStr,
                p_light: light && !latest ? true : null,
            });
            if (error) throw error;
            if (Array.isArray(rows)) data = rows;
        }

        if (latest) {
            dateStr = data.length ? String(data[0].stat_date || '') : '';
            if (!dateStr) throw new Error('No latest daily stats');
        }
        if (data.length === 0 && !TARGET_TEAM && !IS_PROFILE) {
            throw new Error(`No daily stats for ${dateStr}`);
        }

        let updated = '';
        let sponsorUpdated = '';
        // 뷰어십은 값이 없는 날 null로 온다
        const members = data.map(r => {
            updated = !updated || String(r.updated_at || '') > updated ? String(r.updated_at || '') : updated;
            sponsorUpdated =
                !sponsorUpdated || String(r.sponsor_updated_at || '') > sponsorUpdated
                    ? String(r.sponsor_updated_at || '')
                    : sponsorUpdated;

            return {
                id: r.soop_id,
                nickname: r.nickname,
                role: r.role || '',
                team: r.affiliation || null,
                race: r.race || null,
                tier: r.tier || null,
                gender: r.gender || null,
                birthdate: r.birth_date || null,
                birth_month:
                    Number(r.birth_month) || (r.birth_date ? parseInt(String(r.birth_date).split('-')[1], 10) : null),
                balloons: Number(r.balloons || 0),
                broadcast_seconds: Number(r.broadcast_seconds || 0),
                cumulative_viewers: Number(r.cumulative_viewers || 0),
                sponsor_wins: Number(r.sponsor_wins || 0),
                sponsor_losses: Number(r.sponsor_losses || 0),
                sponsor_games: Number(r.sponsor_wins || 0) + Number(r.sponsor_losses || 0),
                viewership_seconds:
                    r.viewership_seconds === null || r.viewership_seconds === undefined
                        ? null
                        : Number(r.viewership_seconds),
            };
        });

        const [year, month] = dateStr.split('-').map(Number);
        return {
            updated_at: formatKstTimestamp(updated),
            date: dateStr,
            year,
            month,
            members,
            sponsor_updated_at: formatKstTimestamp(sponsorUpdated),
            sponsor_month: `${year}-${String(month).padStart(2, '0')}`,
            has_viewership: members.some(m => m.viewership_seconds !== null),
        };
    }

    // 대학 로고(university_logos): 카드가 그려지는 대학 것만 받는다. 표에 없는 대학은 이름 첫 글자 배지로 대신한다.
    const LOGO_URLS = Object.create(null);
    const logoRequests = Object.create(null);
    function loadUniversityLogos(names) {
        const missing = names.filter(n => !logoRequests[n]);
        const client = synergySupabaseClient();
        if (missing.length && client) {
            const request = client
                .rpc('api_university_logos', { p_names: JSON.stringify(missing) })
                .then(({ data, error }) => {
                    if (error) throw error;
                    const base = String(window.SYNERGY_SUPABASE_CONFIG.url).replace(/\/$/, '');
                    (Array.isArray(data) ? data : []).forEach(r => {
                        if (!r || !r.name || !r.path) return;
                        LOGO_URLS[r.name] = `${base}/storage/v1/object/public/staruniv-media/${r.path}`;
                        if (r.color) TEAM_COLORS[r.name] = r.color;
                    });
                })
                .catch(e => {
                    // 실패한 대학은 다음 그리기 때 다시 묻는다
                    missing.forEach(n => {
                        if (logoRequests[n] === request) delete logoRequests[n];
                    });
                    console.warn('대학 로고를 불러오지 못했습니다(이름 첫 글자 배지로 대신합니다)', e);
                });
            missing.forEach(n => {
                logoRequests[n] = request;
            });
        }
        return Promise.all(names.map(n => logoRequests[n]));
    }
    // 개인 페이지는 로고 없이 그 대학 색 하나만 받는다(대학별 한 번).
    const teamColorRequests = Object.create(null);
    function loadTeamColor(team) {
        if (!team) return Promise.resolve(null);
        if (!teamColorRequests[team]) {
            const client = synergySupabaseClient();
            teamColorRequests[team] = !client
                ? Promise.resolve(null)
                : client.rpc('api_university_logos', { p_names: JSON.stringify([team]) }).then(({ data, error }) => {
                      const color = !error && Array.isArray(data) && data[0] ? data[0].color : null;
                      if (color) TEAM_COLORS[team] = color;
                      return color;
                  });
        }
        return teamColorRequests[team];
    }
    function teamLogoUrl(name) {
        return LOGO_URLS[name] || '';
    }
    function teamLogoHtml(name, cls) {
        const url = teamLogoUrl(name);
        const key = `data-team-logo="${escapeHtml(name)}"`;
        return url
            ? `<img src="${escapeHtml(url)}" class="${cls}" alt="" data-fallback="none" ${key}>`
            : `<span class="${cls} team-logo-initial" style="background:${safeCssColor(ownGet(TEAM_COLORS, name))}" aria-hidden="true" ${key}>${escapeHtml(Array.from(name)[0] || '')}</span>`;
    }
    function refreshTeamBranding() {
        document.querySelectorAll('[data-team-logo]').forEach(el => {
            const name = el.dataset.teamLogo;
            if (el.tagName === 'IMG' && el.getAttribute('src') === teamLogoUrl(name)) return;
            el.outerHTML = teamLogoHtml(
                name,
                ['podium-logo', 'rank-logo'].find(c => el.classList.contains(c)) || 'team-logo'
            );
        });
        document.querySelectorAll('[data-team-color]').forEach(el => {
            el.style.background = safeCssColor(ownGet(TEAM_COLORS, el.dataset.teamColor));
        });
    }

    // 일시 오류는 1초 뒤 한 번 더 시도한다
    async function retryOnce(fn) {
        try {
            return await fn();
        } catch (e) {
            await new Promise(resolve => setTimeout(resolve, 1000));
            return fn();
        }
    }
    // 불러오기 실패 안내와 '다시 시도' 버튼(기본은 새로고침)
    function showLoadError(
        host = document.getElementById('grid-container') || document.body,
        onRetry = () => location.reload()
    ) {
        host.innerHTML =
            '<div class="load-msg is-error">데이터를 불러오지 못했습니다.' +
            '<br><button type="button" class="load-retry">다시 시도</button></div>';
        host.querySelector('.load-retry').addEventListener('click', onRetry);
    }

    // 필수 대상이 잘못된 주소는 통계 요청 전에 안내한다.
    let invalidTarget =
        (IS_PROFILE && !/^[a-z0-9_-]+$/.test(PROFILE_ID)) ||
        (PAGE.teamFromUrl && (!TARGET_TEAM || /[\u0000-\u001f\u007f\ufffd]/.test(TARGET_TEAM)));
    if (IS_PROFILE || PAGE.teamFromUrl) {
        try {
            decodeURIComponent(window.location.search);
        } catch (_) {
            invalidTarget = true;
        }
    }
    if (invalidTarget) {
        const message = IS_PROFILE ? '올바른 선수 ID가 필요합니다.' : '올바른 대학 이름이 필요합니다.';
        const host = document.getElementById('grid-container') || document.querySelector('main');
        host.innerHTML = `<div class="load-msg is-error">${message}</div>`;
        return;
    }

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) liveLastRun = 0;
        scheduleLiveStatus();
    });

    // 첫 화면 통계(주소의 날짜, 없으면 최신)를 날짜 목록과 같이 받기 시작한다.
    // 받은 게 비면 목록을 보고 날짜를 고른다. 방송 중 표시도 그리기 전에 미리 받는다.
    const URL_DATE = new URLSearchParams(window.location.search).get('date') || '';
    let latestPrefetch = URL_DATE ? null : loadDailyData('', { latest: true }).catch(() => null);
    let urlDatePrefetch =
        URL_DATE && /^\d{4}-\d{2}-\d{2}$/.test(URL_DATE)
            ? loadDailyData(URL_DATE).then(
                  d => (d && d.members.length ? d : null),
                  () => null
              )
            : null;
    // 방송 중 표시 미리 받기: 목록 화면은 ID 목록, 개인 페이지는 그 사람 정보(처음 한 번만 쓴다)
    let profileLivePrefetch = null;
    if (IS_PROFILE) {
        profileLivePrefetch = checkIsLiveRealtime(PROFILE_ID);
        profileLivePrefetch.catch(() => {});
    } else {
        liveIdsPrefetch = fetchLiveIds();
        liveIdsPrefetch.catch(() => {});
    }
    // 날짜 목록은 달력과 지난달 순위 비교에만 쓴다. 첫 화면 통계가 있으면 목록을 기다리지 않고 먼저 그린다.
    const datesRequest = retryOnce(loadAvailableDates);
    datesRequest.catch(() => {});
    let initialData = latestPrefetch ? await latestPrefetch : urlDatePrefetch ? await urlDatePrefetch : null;
    latestPrefetch = urlDatePrefetch = null;
    const initialDate = initialData && initialData.date;
    let AVAILABLE_DATES;
    if (initialDate) {
        AVAILABLE_DATES = [initialDate];
        datesRequest.then(
            list => {
                if (!Array.isArray(list) || !list.length) return;
                AVAILABLE_DATES = list.includes(initialDate) ? list : [...list, initialDate].sort().reverse();
                updateDateChrome();
                const cached = fetchedData.get(currentDateStr);
                if (!IS_PROFILE && cached)
                    attachRankBadges(
                        computeTeamAggregates(cached.members, metricDefs[currentMetric]),
                        currentDateStr,
                        currentMetric
                    );
            },
            e => console.warn('날짜 목록을 불러오지 못했습니다(최신 날짜만 보여 줍니다)', e)
        );
    } else {
        try {
            AVAILABLE_DATES = await datesRequest;
        } catch (e) {
            console.error('날짜 목록을 불러오지 못했습니다', e);
            showLoadError();
            return;
        }
    }
    if (AVAILABLE_DATES.length === 0) {
        const host = document.getElementById('grid-container') || document.body;
        host.innerHTML = '<div class="load-msg is-error">표시할 데이터가 없습니다.</div>';
        return;
    }

    let currentDateStr = AVAILABLE_DATES[0];
    let currentMetric = 'balloon';
    // 오래 열린 페이지에서 무제한 쌓이지 않게 최근 32개만 보관한다.
    function createDateCache(maxAge = () => Infinity) {
        const entries = new Map();
        return {
            get(date) {
                if (!entries.has(date)) return undefined;
                const value = entries.get(date);
                entries.delete(date);
                if (Date.now() - value.storedAt >= maxAge(date)) return undefined;
                entries.set(date, value);
                return value.data;
            },
            set(date, data) {
                entries.delete(date);
                entries.set(date, { data, storedAt: Date.now() });
                if (entries.size > 32) entries.delete(entries.keys().next().value);
            },
        };
    }
    // 최신은 5분, 과거는 1시간 지난 값을 다시 받는다.
    const cacheMaxAge = date => (date === AVAILABLE_DATES[0] ? 5 : 60) * 60 * 1000;
    const fetchedData = createDateCache(cacheMaxAge);
    const rankOnlyData = createDateCache(cacheMaxAge);
    // 같은 날짜 요청이 떠 있으면 그 요청을 같이 기다린다
    const pendingLoads = Object.create(null);
    function loadOnce(key, load) {
        if (!pendingLoads[key])
            pendingLoads[key] = load().finally(() => {
                delete pendingLoads[key];
            });
        return pendingLoads[key];
    }
    // 늦게 온 이전 응답이 최신 화면을 덮지 않게 요청 번호로 최신 요청의 응답만 그린다.
    let renderToken = 0;
    const playerSearch = document.getElementById('player-search');
    const playerSearchResults = document.getElementById('player-search-results');
    let searchPlayers = null;
    let searchPlayersRequest = null;
    let searchPlayersFailed = false;

    function loadSearchPlayers() {
        if (searchPlayers || searchPlayersRequest) return;
        searchPlayersFailed = false;
        searchPlayersRequest = (async () => {
            try {
                const client = synergySupabaseClient();
                if (!client) throw new Error('검색 연결 설정이 없습니다');
                // 통계표에 없는 휴면 선수도 포함하는 공개 검색 명단.
                const { data, error } = await client.rpc('elo_players_list', { p_ranked: false });
                if (error) throw error;
                if (!data || !Array.isArray(data.c) || !Array.isArray(data.r)) throw new Error('검색 명단 형식 오류');
                searchPlayers = data.r.map(values => {
                    const row = Object.fromEntries(data.c.map((key, i) => [key, values[i]]));
                    return {
                        id: row.soop_id,
                        nickname: row.nickname || row.elo_name,
                        alias: row.elo_name,
                        team: row.affiliation,
                    };
                });
            } catch (e) {
                searchPlayersFailed = true;
            } finally {
                searchPlayersRequest = null;
                if (document.activeElement === playerSearch && !playerSearchResults.hidden) renderPlayerSearch();
            }
        })();
    }

    function closePlayerSearch() {
        if (!playerSearchResults) return;
        playerSearchResults.hidden = true;
        playerSearch.setAttribute('aria-expanded', 'false');
    }

    function renderPlayerSearch() {
        if (!playerSearchResults) return;
        const query = playerSearch.value.trim().toLocaleLowerCase();
        if (!query) return closePlayerSearch();
        const data = fetchedData.get(currentDateStr);
        const players = new Map();
        for (const m of [...(data ? data.members : []), ...(searchPlayers || [])]) {
            if (m.id) players.set(String(m.id).toLowerCase(), m);
        }
        // 닉네임·이름·소속 어느 것에 들어 있어도 찾는다
        const matches = [...players.values()].filter(m =>
            [m.nickname, m.alias, m.team].some(name =>
                String(name || '')
                    .toLocaleLowerCase()
                    .includes(query)
            )
        );
        const message = searchPlayersFailed
            ? '전체 선수 검색을 불러오지 못했습니다'
            : searchPlayersRequest
              ? '선수 목록을 불러오는 중'
              : '검색 결과가 없습니다';
        const notice = `<div class="player-search-empty" role="status">${message}</div>`;
        playerSearchResults.innerHTML = matches.length
            ? matches
                  .map(
                      m =>
                          `<a class="player-search-result" href="${escapeHtml(LOGO_PREFIX)}profile.html?id=${attrUrlParam(m.id)}&date=${attrUrlParam(currentDateStr)}&metric=${attrUrlParam(currentMetric)}&fromTeam=${attrUrlParam(TARGET_TEAM || '')}"><span class="player-search-avatar"><span>${escapeHtml(String(m.nickname || '?').slice(0, 1))}</span><img src="${escapeHtml(soopPhotoUrl(m.id))}" alt="" loading="lazy" data-fallback="hidden"></span><span class="player-search-name" title="${escapeHtml(m.nickname)}">${escapeHtml(m.nickname)}</span><span class="player-search-team" title="${escapeHtml(m.team || 'FA')}">${escapeHtml(m.team || 'FA')}</span></a>`
                  )
                  .join('') + (searchPlayersFailed ? notice : '')
            : notice;
        playerSearchResults.hidden = false;
        playerSearch.setAttribute('aria-expanded', 'true');
    }

    const calBtn = document.getElementById('calendar-btn');
    const metricBtn = document.getElementById('metric-btn');
    let viewershipAvailable = false;
    function currentMetricDef() {
        return ownGet(metricDefs, currentMetric) || metricDefs.balloon;
    }
    function updateMetricChrome() {
        if (!metricBtn) return;
        metricBtn.innerHTML =
            escapeHtml(currentMetricDef().label) + '<span class="nav-chevron" aria-hidden="true"></span>';
    }

    function initializePage() {
        const params = new URLSearchParams(window.location.search);
        if (params.get('date') && AVAILABLE_DATES.includes(params.get('date'))) currentDateStr = params.get('date');
        if (ownGet(metricDefs, params.get('metric'))) currentMetric = params.get('metric');

        updateMetricChrome();
        applyData();
    }

    if (document.readyState === 'loading') {
        window.addEventListener('DOMContentLoaded', initializePage, { once: true });
    } else {
        initializePage();
    }

    // 로고는 1.5초 안에 오면 첫 화면부터 쓰고, 늦으면 이름 첫 글자 배지로 먼저 그린 뒤 갱신한다.
    // 개인 페이지는 로고를 그리지 않는다(색은 loadTeamColor).
    function renderDashboardWithLogos(data, token) {
        const names = TARGET_TEAM ? [TARGET_TEAM] : [...new Set(data.members.map(m => m.team).filter(Boolean))];
        let done = false;
        const ready = loadUniversityLogos(names).then(() => {
            done = true;
        });
        let timer;
        return Promise.race([
            ready,
            new Promise(resolve => {
                timer = setTimeout(resolve, 1500);
            }),
        ])
            .then(() => {
                if (token !== renderToken) return;
                renderDashboard(data);
                if (!done)
                    ready.then(() => {
                        if (token === renderToken) refreshTeamBranding();
                    });
            })
            .finally(() => clearTimeout(timer));
    }

    function applyData() {
        const token = ++renderToken;
        updateDateChrome();

        const cached = fetchedData.get(currentDateStr);
        if (cached) {
            if (IS_PROFILE) profileLoaded(currentDateStr);
            IS_PROFILE ? renderProfile(cached) : renderDashboardWithLogos(cached, token);
            return;
        }

        const gridEl = document.getElementById('grid-container');
        if (!IS_PROFILE && gridEl) {
            gridEl.innerHTML = '<div class="load-msg">불러오는 중</div>';
        }
        if (IS_PROFILE) profileBusy(true);
        const requestedDate = currentDateStr;
        // 미리 받은 최신 통계가 이 날짜면 그것을 쓴다(한 번만)
        const prefetched = initialData && initialData.date === requestedDate ? initialData : null;
        initialData = null;
        const load = () =>
            Promise.resolve(prefetched).then(data =>
                data && data.date === requestedDate ? data : retryOnce(() => loadDailyData(requestedDate))
            );
        loadOnce('full:' + requestedDate, load)
            .then(normalized => {
                fetchedData.set(requestedDate, normalized);
                // 그사이 날짜/지표가 바뀌었으면 낡은 응답이라 그리지 않는다(캐시에는 남긴다).
                if (token !== renderToken) return;
                if (IS_PROFILE) profileLoaded(requestedDate);
                IS_PROFILE ? renderProfile(normalized) : renderDashboardWithLogos(normalized, token);
            })
            .catch(() => {
                if (token !== renderToken) return;
                if (IS_PROFILE) profileLoadFailed(requestedDate);
                else if (gridEl)
                    showLoadError(gridEl, () => {
                        if (token === renderToken) applyData();
                    });
            });
    }

    // 개인 페이지: 날짜를 바꾸는 동안 카드를 흐리게 하고, 실패하면 이전 수치가 새 값처럼 보이지 않게
    // 날짜를 지금 보이는 데이터의 날짜로 되돌린다.
    let profileShownDate = null;
    function profileBusy(on) {
        const card = document.getElementById('profile-card');
        if (!card) return;
        card.style.opacity = on ? '0.5' : '';
        if (on) card.setAttribute('aria-busy', 'true');
        else card.removeAttribute('aria-busy');
    }
    function profileNotice() {
        let el = document.getElementById('profile-load-notice');
        const card = document.getElementById('profile-card');
        if (!el && card) {
            el = document.createElement('div');
            el.id = 'profile-load-notice';
            el.setAttribute('role', 'alert');
            card.parentNode.insertBefore(el, card);
        }
        return el;
    }
    function profileLoaded(date) {
        profileShownDate = date;
        profileBusy(false);
        const el = document.getElementById('profile-load-notice');
        if (el) el.remove();
    }
    function profileLoadFailed(failedDate) {
        profileBusy(false);
        const host = profileNotice();
        if (!host) return;
        const retry = () => {
            currentDateStr = failedDate;
            applyData();
        };
        if (!profileShownDate) {
            showLoadError(host, retry);
            return;
        }
        currentDateStr = profileShownDate;
        updateDateChrome();
        const label = d => {
            const p = d.split('-');
            return `${Number(p[1])}월 ${Number(p[2])}일`;
        };
        host.innerHTML =
            '<div class="load-notice">' +
            escapeHtml(label(failedDate)) +
            ' 데이터를 불러오지 못해 ' +
            escapeHtml(label(profileShownDate)) +
            ' 데이터를 보여 줍니다.<br><button type="button" class="load-retry">다시 시도</button></div>';
        host.querySelector('.load-retry').addEventListener('click', retry);
    }

    // 지표는 그 달 1일부터 누적이라 그 달 마지막 데이터 날짜가 최종이다.
    // 단 가장 최근 달은 말일 데이터가 오기 전까지 '진행 중'이다.
    function isMonthFinal(dateStr) {
        const ym = dateStr.slice(0, 7);
        if (AVAILABLE_DATES.find(d => d.startsWith(ym)) !== dateStr) return false;
        if (ym !== AVAILABLE_DATES[0].slice(0, 7)) return true;
        const [y, m, d] = dateStr.split('-').map(Number);
        return d === new Date(y, m, 0).getDate();
    }
    // 달마다 마지막 데이터 날짜(최근 달부터 6개)
    function monthShortcuts() {
        const seen = new Set();
        return AVAILABLE_DATES.filter(d => {
            const ym = d.slice(0, 7);
            if (seen.has(ym)) return false;
            seen.add(ym);
            return true;
        }).slice(0, 6);
    }

    function updateDateChrome() {
        const parts = currentDateStr.split('-');
        if (calBtn) {
            // 그 달 최종 날짜면 '최종'으로 보인다
            calBtn.innerHTML =
                escapeHtml(parts[0].slice(-2)) +
                '년 ' +
                escapeHtml(parts[1]) +
                '월 ' +
                (isMonthFinal(currentDateStr) ? '최종' : escapeHtml(parts[2]) + '일') +
                '<span class="nav-chevron" aria-hidden="true"></span>';
        }

        if (TARGET_TEAM && !IS_PROFILE) {
            const teamBackLink = document.getElementById('back-link');
            if (teamBackLink) {
                teamBackLink.href =
                    'index.html?date=' +
                    encodeURIComponent(currentDateStr) +
                    '&metric=' +
                    encodeURIComponent(currentMetric);
            }
        } else if (IS_PROFILE) {
            // 뒤로가기 링크가 지금 날짜를 들고 가도록 매번 다시 계산한다.
            const backHref = FROM_TEAM
                ? `team.html?team=${encodeURIComponent(FROM_TEAM)}&date=${encodeURIComponent(currentDateStr)}&metric=${encodeURIComponent(currentMetric)}`
                : `index.html?date=${encodeURIComponent(currentDateStr)}&metric=${encodeURIComponent(currentMetric)}`;
            const backLinkEl = document.getElementById('back-link');
            if (backLinkEl) backLinkEl.href = LOGO_PREFIX + backHref;
        }
    }

    function findPrevMonthDate(dateStr) {
        const [y, m] = dateStr.split('-').map(Number);
        const prevYM = m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, '0')}`;
        const candidates = AVAILABLE_DATES.filter(d => d.startsWith(prevYM));
        if (candidates.length === 0) return null;
        return candidates.slice().sort().reverse()[0];
    }

    const aggregateCache = new WeakMap();
    function computeTeamAggregates(members, def) {
        members = members || [];
        let byMetric = aggregateCache.get(members);
        if (!byMetric) aggregateCache.set(members, (byMetric = new Map()));
        if (byMetric.has(def)) return byMetric.get(def);
        const teams = Object.create(null);
        (members || []).forEach(m => {
            const t = m.team || '미분류';
            if (['FA', '휴면', '미분류'].includes(t)) return;
            if (!teams[t]) teams[t] = [];
            teams[t].push(m);
        });

        const result = [];
        for (const [tName, tMembers] of Object.entries(teams)) {
            let mSum = 0,
                fSum = 0,
                fCount = 0,
                tCount = 0;
            let males = [],
                females = [];

            tMembers.forEach(m => {
                const v = m[def.field] || 0;
                const counted = v !== 0 && !isExcludedRole(m);

                if (m.gender === '여자') {
                    females.push(m);
                    if (counted) {
                        fSum += v;
                        fCount++;
                        tCount++;
                    }
                } else {
                    males.push(m);
                    if (counted) {
                        mSum += v;
                        tCount++;
                    }
                }
            });

            const byValue = (a, b) => (b[def.field] || 0) - (a[def.field] || 0);
            males.sort(byValue);
            females.sort(byValue);
            const fAvg = fCount > 0 ? Math.round(fSum / fCount) : 0;
            const tAvg = tCount > 0 ? Math.round((mSum + fSum) / tCount) : 0;

            result.push({
                name: tName,
                males,
                females,
                fAvg,
                tAvg,
                totalSum: mSum + fSum,
                counted: tCount,
            });
        }
        // 대학 순서는 지표와 상관없이 전체 평균으로 매긴다
        result.sort((a, b) => b.tAvg - a.tAvg);
        byMetric.set(def, result);
        return result;
    }

    function computeTeamRanks(data, metricKey) {
        const def = ownGet(metricDefs, metricKey);
        if (!def) return {};
        const teamStats = computeTeamAggregates(data.members, def);
        const ranks = Object.create(null);
        teamStats.forEach((s, i) => {
            ranks[s.name] = i + 1;
        });
        return ranks;
    }

    async function attachRankBadges(teamStats, dateStr, metricKey) {
        if (TARGET_TEAM) return;
        const currentRanks = {};
        teamStats.forEach((s, i) => {
            currentRanks[s.name] = i + 1;
        });

        const prevDate = findPrevMonthDate(dateStr);
        if (!prevDate) return;

        const token = renderToken;
        let prevData = fetchedData.get(prevDate) || rankOnlyData.get(prevDate);
        if (!prevData) {
            try {
                prevData = await loadOnce('light:' + prevDate, () => loadDailyData(prevDate, { light: true }));
                rankOnlyData.set(prevDate, prevData);
            } catch (e) {
                return;
            }
        }
        // 지난달 뷰어십이 없으면 모두 0이라 비교하지 않는다
        if (metricKey === 'viewership' && !prevData.has_viewership) return;
        // await 사이에 날짜/지표가 바뀌었으면 엉뚱한 증감 화살표가 남지 않게 멈춘다.
        if (token !== renderToken) return;

        const prevRanks = computeTeamRanks(prevData, metricKey);
        teamStats.forEach(s => {
            const slot = document.querySelector('.rank-badge-slot[data-team="' + CSS.escape(s.name) + '"]');
            if (!slot) return;
            const curRank = currentRanks[s.name];
            if (!Object.prototype.hasOwnProperty.call(prevRanks, s.name)) {
                slot.innerHTML = '<span class="rank-change new">NEW</span>';
            } else {
                const prevRank = prevRanks[s.name];
                if (curRank < prevRank)
                    slot.innerHTML = '<span class="rank-change up">▲' + (prevRank - curRank) + '</span>';
                else if (curRank > prevRank)
                    slot.innerHTML = '<span class="rank-change down">▼' + (curRank - prevRank) + '</span>';
                else slot.innerHTML = '<span class="rank-change same">-</span>';
            }
        });
    }

    // 전체 페이지 맨 위 순위 패널(평균·합계·개인). 고른 쪽·펼침은 날짜·지표를 바꿔도 유지한다.
    // 개인은 TOP 15(값 0, 휴면, 수장·전력외 제외)이며, 상위 % 색칠과 달리 FA도 넣는다.
    const RANK_TOP = 3;
    const PERSON_TOP = 15;
    let rankView = 'avg';
    let rankOpen = false;
    function renderRankSummary(teamStats, people, def) {
        const box = document.getElementById('rank-summary');
        if (!box || TARGET_TEAM) return;
        if (!teamStats.length) {
            box.style.display = 'none';
            box.innerHTML = '';
            return;
        }
        const query = `&date=${attrUrlParam(currentDateStr)}&metric=${attrUrlParam(currentMetric)}`;
        const teamsBy = key =>
            teamStats
                .slice()
                .sort((a, b) => b[key] - a[key])
                .map(ts => ({
                    href: `team.html?team=${attrUrlParam(ts.name)}${query}`,
                    name: ts.name,
                    pic: cls => teamLogoHtml(ts.name, cls),
                    sub: `집계 ${ts.counted}명`,
                    value: def.format(ts[key]),
                }));
        const personRows = people.slice(0, PERSON_TOP).map(m => ({
            href: m.id ? `${escapeHtml(LOGO_PREFIX)}profile.html?id=${attrUrlParam(m.id)}${query}` : '',
            name: m.nickname,
            pic: cls =>
                `<img class="${cls} is-photo" src="${escapeHtml(soopPhotoUrl(m.id))}" alt="" loading="lazy" data-fallback="hidden">`,
            sub: m.team || 'FA',
            value: def.format(m[def.field] || 0),
        }));
        // [고르기 키, 단추 글자, 패널 제목, 제목 아이콘, 줄]
        const VIEWS = [
            ['avg', '평균', '대학 순위', 'trophy', teamsBy('tAvg')],
            ['sum', '합계', '대학 순위', 'trophy', teamsBy('totalSum')],
            ['person', '개인', '개인 순위', 'person', personRows],
        ];
        const nameHtml = r => (r.href ? `<a href="${r.href}">${escapeHtml(r.name)}</a>` : escapeHtml(r.name));
        // 이름 아래 줄은 대학이면 집계 인원, 개인이면 소속 대학
        const MEDALS = ['gold', 'silver', 'bronze'];
        const podium = (r, i) =>
            `<div class="podium-card ${MEDALS[i]}"><span class="podium-medal">${i + 1}</span>${r.pic('podium-logo')}` +
            `<div class="podium-info"><span class="podium-name">${nameHtml(r)}</span><span class="podium-sub">${escapeHtml(r.sub)}</span></div>` +
            `<div class="podium-value">${r.value}${def.count ? `<span class="podium-unit">${def.count}</span>` : ''}</div></div>`;
        const restRow = (r, i) =>
            `<li class="rank-row"><span class="rank-no">${i + 1}</span><span class="rank-name">${r.pic('rank-logo')}${nameHtml(r)}${
                r.sub ? `<span class="rank-sub">${escapeHtml(r.sub)}</span>` : ''
            }</span><span class="rank-value">${r.value}</span></li>`;
        const moreLabel = () => (rankOpen ? '접기' : '더 보기');
        const view = ([key, , , , rows]) =>
            `<div class="rank-view" data-view="${key}"${rankView === key ? '' : ' hidden'}><div class="rank-podium">${rows
                .slice(0, RANK_TOP)
                .map(podium)
                .join('')}</div>` +
            (rows.length > RANK_TOP
                ? `<ol class="rank-list">${rows
                      .slice(RANK_TOP)
                      .map((r, i) => restRow(r, i + RANK_TOP))
                      .join(
                          ''
                      )}</ol><button type="button" class="rank-more chev" aria-expanded="${rankOpen}">${moreLabel()}</button>`
                : '') +
            '</div>';
        const tab = ([key, label]) =>
            `<button type="button" class="rank-tab${rankView === key ? ' active' : ''}" data-view="${key}" aria-pressed="${rankView === key}">${label}</button>`;
        const titleHtml = key => {
            const [, , title, iconName] = VIEWS.find(v => v[0] === key);
            return `<span class="panel-icon">${icon(iconName, 20)}</span><span>${title}</span>`;
        };
        box.classList.toggle('is-open', rankOpen);
        box.innerHTML =
            `<div class="rank-head"><h2 class="rank-title">${titleHtml(rankView)}</h2><div class="rank-tabs">${VIEWS.map(tab).join('')}</div></div>` +
            VIEWS.map(view).join('');
        const title = box.querySelector('.rank-title');
        box.querySelectorAll('.rank-tab').forEach(btn =>
            btn.addEventListener('click', () => {
                rankView = btn.dataset.view;
                title.innerHTML = titleHtml(rankView);
                box.querySelectorAll('.rank-tab').forEach(b => {
                    b.classList.toggle('active', b === btn);
                    b.setAttribute('aria-pressed', String(b === btn));
                });
                box.querySelectorAll('.rank-view').forEach(v => (v.hidden = v.dataset.view !== rankView));
            })
        );
        box.querySelectorAll('.rank-more').forEach(btn =>
            btn.addEventListener('click', () => {
                rankOpen = !rankOpen;
                box.classList.toggle('is-open', rankOpen);
                box.querySelectorAll('.rank-more').forEach(b => {
                    b.textContent = moreLabel();
                    b.setAttribute('aria-expanded', String(rankOpen));
                });
            })
        );
        box.style.display = '';
    }

    // 선 아이콘. size는 px
    const ICON_PATHS = {
        sum: '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v5c0 1.66 3.13 3 7 3s7-1.34 7-3V6"/><path d="M5 11v5c0 1.66 3.13 3 7 3s7-1.34 7-3v-5"/>',
        female: '<circle cx="12" cy="9" r="5"/><path d="M12 14v7M9 18h6"/>',
        avg: '<path d="M4 20V10M12 20V4M20 20v-7"/>',
        trophy: '<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0V4zM7 6H4v2a3 3 0 0 0 3 3M17 6h3v2a3 3 0 0 1-3 3"/>',
        person: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.42 3.58-8 8-8s8 3.58 8 8"/>',
        group: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6.5 6.5-6.5s6.5 2.9 6.5 6.5"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 13.8c2.1.7 3.5 2.8 3.5 5.2"/>',
    };
    const icon = (name, size, cls = '') =>
        `<svg${cls ? ` class="${cls}"` : ''} viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON_PATHS[name]}</svg>`;

    const TIER_ORDER = ['갓', '킹', '잭', '조커', '스페이드', '0', '1', '2', '3', '4', '5', '6', '7', '8', '베이비'];

    let faBarCollapsed = true;

    function renderFaBar(data) {
        if (TARGET_TEAM) return;
        const faBar = document.getElementById('fa-bar');
        if (!faBar) return;

        const faMembers = (data.members || []).filter(m => m.team === 'FA').slice();
        if (faMembers.length === 0) {
            faBar.style.display = 'none';
            faBar.innerHTML = '';
            return;
        }

        faMembers.sort((a, b) => {
            const ai = TIER_ORDER.indexOf(a.tier);
            const bi = TIER_ORDER.indexOf(b.tier);
            return (ai === -1 ? TIER_ORDER.length : ai) - (bi === -1 ? TIER_ORDER.length : bi);
        });

        const itemsHtml = faMembers
            .map(m => {
                // id·닉네임은 외부 입력이라 모든 삽입 지점을 escapeHtml / attrUrlParam으로 감싼다(저장형 XSS).
                const photoImg = `<img class="fa-bar-photo" src="${escapeHtml(soopPhotoUrl(m.id))}" alt="" loading="lazy" data-fallback="hidden">`;
                const liveDot = m.id ? `<span class="live-dot" data-live-id="${escapeHtml(m.id)}"></span>` : '';
                const inner = `${photoImg}${liveDot}<span class="member-name">${escapeHtml(m.nickname)}</span>`;
                return m.id
                    ? `<div class="fa-bar-item"><a href="${escapeHtml(LOGO_PREFIX)}profile.html?id=${attrUrlParam(m.id)}&date=${attrUrlParam(currentDateStr)}&metric=${attrUrlParam(currentMetric)}&fromTeam=${attrUrlParam(TARGET_TEAM || '')}">${inner}</a></div>`
                    : `<div class="fa-bar-item"><span class="fa-bar-static">${inner}</span></div>`;
            })
            .join('');

        // 머리 줄 어디를 눌러도 펼쳐진다. 인원은 대학 카드 머리와 같은 형식('여자'가 아니면 남자 칸)
        const faFemale = faMembers.filter(m => m.gender === '여자').length;
        const faCountText = `총 ${faMembers.length}명 · 남 ${faMembers.length - faFemale} · 여 ${faFemale}`;
        const faMoreLabel = () => (faBarCollapsed ? '펼치기' : '접기');
        faBar.innerHTML = `<button type="button" class="fa-bar-title" id="fa-bar-toggle" aria-expanded="${!faBarCollapsed}"><span class="panel-icon">${icon('group', 20)}</span><span class="fa-bar-label">FA</span><span class="team-count">${faCountText}</span><span class="fa-bar-more chev">${faMoreLabel()}</span></button><div class="fa-bar-list${faBarCollapsed ? ' collapsed' : ''}" id="fa-bar-list">${itemsHtml}</div>`;
        faBar.style.display = '';

        const toggleEl = document.getElementById('fa-bar-toggle');
        const listEl = document.getElementById('fa-bar-list');
        if (toggleEl && listEl) {
            toggleEl.addEventListener('click', () => {
                faBarCollapsed = !faBarCollapsed;
                toggleEl.setAttribute('aria-expanded', String(!faBarCollapsed));
                toggleEl.querySelector('.fa-bar-more').textContent = faMoreLabel();
                listEl.classList.toggle('collapsed', faBarCollapsed);
            });
        }
    }

    // 뷰어십은 그 날짜에 값이 있을 때만 고를 수 있다. 없으면 숨기고 보고 있었으면 별풍선으로 돌린다.
    function syncViewershipMetric(data) {
        viewershipAvailable = !!(data && data.has_viewership);
        if (!viewershipAvailable && currentMetric === 'viewership') {
            currentMetric = 'balloon';
            updateMetricChrome();
        }
    }

    function renderDashboard(data) {
        syncViewershipMetric(data);
        const def = currentMetricDef();
        const members = (data && data.members) || [];
        const monthNum = parseInt(currentDateStr.split('-')[1], 10);
        const upd = currentMetric === 'sponsor' && data.sponsor_updated_at ? data.sponsor_updated_at : data.updated_at;
        const source = SOURCES[def.source];

        const validTeams = new Set(members.map(m => m.team).filter(t => t && t !== 'FA' && t !== '휴면'));
        // 팀 페이지에서는 전체 집계 문구를 보이지 않는다.
        const teamCountHtml = TARGET_TEAM
            ? ''
            : validTeams.size +
              '팀 · ' +
              members.filter(m => m.team && m.team !== 'FA' && m.team !== '휴면').length +
              '명 · ';
        const metaEl = document.getElementById('top-meta-text');
        // upd는 데이터에서 온 값이라 escape한다.
        if (metaEl) {
            metaEl.innerHTML =
                teamCountHtml +
                '갱신 ' +
                escapeHtml(upd || '-') +
                ' · 출처 <a href="' +
                escapeHtml(source.url) +
                '" target="_blank" rel="noopener" class="source-link">' +
                escapeHtml(source.name) +
                '</a>';
        }

        // 멤버 객체를 키로 쓴다(문자열 키는 같은 팀 동명이인이 섞인다).
        const tiers = new Map();
        const pool = members
            .filter(m => {
                const v = m[def.field] || 0;
                if (v === 0) return false;
                if (isExcludedRole(m)) return false;
                if (!m.team || ['FA', '휴면'].includes(m.team)) return false;
                return true;
            })
            .sort((a, b) => (b[def.field] || 0) - (a[def.field] || 0));

        // 맨 위 개인 순위는 FA도 넣는다(색칠 기준 pool과 따로)
        const people = members
            .filter(m => (m[def.field] || 0) > 0 && !isExcludedRole(m) && m.team !== '휴면')
            .sort((a, b) => (b[def.field] || 0) - (a[def.field] || 0));

        const n = pool.length;
        const t1 = Math.max(1, Math.round(n * 0.01));
        const t5 = Math.max(1, Math.round(n * 0.05));
        const t10 = Math.max(1, Math.round(n * 0.1));
        pool.forEach((m, i) => {
            if (i < t1) tiers.set(m, 'tier1');
            else if (i < t5) tiers.set(m, 'tier5');
            else if (i < t10) tiers.set(m, 'tier10');
        });

        const teamStats = computeTeamAggregates(members, def);

        let html = '';
        const targetTeamStats = TARGET_TEAM ? teamStats.filter(t => t.name === TARGET_TEAM) : teamStats;

        if (TARGET_TEAM && targetTeamStats.length === 0) {
            html = '<div class="team-card team-empty">이 날짜에는 팀 정보가 없습니다.</div>';
        } else {
            targetTeamStats.forEach(ts => {
                const topColor = safeCssColor(ownGet(TEAM_COLORS, ts.name));
                const logoHtml = teamLogoHtml(ts.name, 'team-logo');
                const countHtml = `<span class="team-count">총 ${ts.males.length + ts.females.length}명 · 남 ${ts.males.length} · 여 ${ts.females.length}</span>`;
                const headerLeft = TARGET_TEAM
                    ? `<div class="team-header-left">${logoHtml}<span class="team-name">${escapeHtml(ts.name)}</span>${countHtml}</div>`
                    : `<div class="team-header-left">${logoHtml}<a class="team-link team-name" href="team.html?team=${attrUrlParam(ts.name)}&date=${attrUrlParam(currentDateStr)}&metric=${attrUrlParam(currentMetric)}">${escapeHtml(ts.name)}</a>${countHtml}</div>`;
                const rankSlot = TARGET_TEAM
                    ? ''
                    : `<span class="rank-badge-slot" data-team="${escapeHtml(ts.name)}"></span>`;

                const makeRows = (list, padLen) => {
                    let rHtml = list
                        .map(m => {
                            let cClass = [];
                            if (isExcludedRole(m)) cClass.push('excluded');

                            const tier = tiers.get(m);
                            if (tier) cClass.push(tier);

                            const isBday = m.birth_month === monthNum;
                            const liveDot = m.id
                                ? `<span class="live-dot" data-live-id="${escapeHtml(m.id)}"></span>`
                                : '';
                            const bdayMark = isBday ? '<span class="bday-mark">🎂</span>' : '';
                            const nameContent = m.id
                                ? `${liveDot}<a class="member-name-link" href="${escapeHtml(LOGO_PREFIX)}profile.html?id=${attrUrlParam(m.id)}&date=${attrUrlParam(currentDateStr)}&metric=${attrUrlParam(currentMetric)}&fromTeam=${attrUrlParam(TARGET_TEAM || '')}">${escapeHtml(m.nickname)}</a>${bdayMark}`
                                : liveDot + escapeHtml(m.nickname) + bdayMark;
                            return `<div class="member-row ${cClass.join(' ')}"><span class="member-name">${nameContent}</span><span class="member-value">${def.format(m[def.field] || 0)}</span></div>`;
                        })
                        .join('');

                    for (let i = 0; i < padLen - list.length; i++)
                        rHtml += `<div class="member-row empty"><span class="member-name"></span><span class="member-value"></span></div>`;
                    return rHtml;
                };

                const maxLen = Math.max(ts.males.length, ts.females.length);
                html += `
              <div class="team-card">
                <div class="team-card-topbar" data-team-color="${escapeHtml(ts.name)}" style="background:${topColor};"></div>
                <div class="team-header">${headerLeft}${rankSlot}</div>
                <div class="member-columns">
                  <div class="member-col"><div class="member-col-label"><span>남자</span><span>${def.unit}</span></div>${makeRows(ts.males, maxLen)}</div>
                  <div class="member-col"><div class="member-col-label"><span>여자</span><span>${def.unit}</span></div>${makeRows(ts.females, maxLen)}</div>
                </div>
                <div class="team-footer">
                  <div class="stat-card"><div class="stat-card-header">${icon('sum', 12, 'stat-icon')}<span class="stat-label">전체 합계</span></div><div class="stat-value">${def.format(ts.totalSum)}</div></div>
                  <div class="stat-card female-avg"><div class="stat-card-header">${icon('female', 12, 'stat-icon')}<span class="stat-label">여자 평균</span></div><div class="stat-value">${def.format(ts.fAvg)}</div></div>
                  <div class="stat-card total-avg"><div class="stat-card-header">${icon('avg', 12, 'stat-icon')}<span class="stat-label">전체 평균</span></div><div class="stat-value">${def.format(ts.tAvg)}</div></div>
                </div>
              </div>`;
            });
        }
        const gridEl = document.getElementById('grid-container');
        if (gridEl) gridEl.innerHTML = `<div class="grid ${TARGET_TEAM ? 'single-team' : ''}">${html}</div>`;
        attachRankBadges(teamStats, currentDateStr, currentMetric);
        renderFaBar(data);
        renderRankSummary(teamStats, people, def);
        if (playerSearch && document.activeElement === playerSearch) renderPlayerSearch();
        if (TARGET_TEAM) document.title = TARGET_TEAM + ' 현황';
        scheduleLiveStatus();
    }

    function renderProfile(data) {
        const tid = PROFILE_ID;
        const member = ((data && data.members) || []).find(m => m.id === tid);
        const metaEl = document.getElementById('top-meta-text');
        const cardEl = document.getElementById('profile-card');
        if (!member) {
            refreshProfileLive = null;
            const liveEl = document.getElementById('profile-live-embed');
            if (liveEl) {
                liveEl.innerHTML = '';
                liveEl.style.display = 'none';
            }
            if (cardEl) cardEl.style.display = 'none';
            if (metaEl) metaEl.textContent = '데이터 없음';
            return;
        }

        const topbar = document.getElementById('profile-topbar');
        topbar.style.background = safeCssColor(ownGet(TEAM_COLORS, member.team));
        loadTeamColor(member.team)
            .then(color => {
                if (color && topbar.dataset.team === member.team) topbar.style.background = safeCssColor(color);
            })
            .catch(() => {});
        topbar.dataset.team = member.team || '';
        document.getElementById('profile-nickname').textContent = member.nickname || '';
        document.getElementById('profile-card').style.display = '';

        document.getElementById('profile-photo').src = soopPhotoUrl(tid);

        const liveEmbedEl = document.getElementById('profile-live-embed');
        if (tid && liveEmbedEl) {
            refreshProfileLive = async () => {
                const liveToken = renderToken;
                const prefetchedLive = profileLivePrefetch;
                profileLivePrefetch = null;
                const result = await (prefetchedLive || checkIsLiveRealtime(tid));
                // 조회가 끝났을 때 화면이 다른 날짜/사람으로 넘어갔으면 얹지 않는다.
                if (liveToken !== renderToken) {
                    liveLastRun = 0;
                    return;
                }
                if (document.hidden) return;
                if (result && result.broad) {
                    const { broad, broadStart } = result;
                    const viewerText =
                        broad.current_sum_viewer != null
                            ? broad.current_sum_viewer.toLocaleString('ko-KR') + '명 시청 중'
                            : '';
                    let elapsedText = '';
                    if (broadStart) {
                        const startDate = new Date(broadStart.replace(' ', 'T'));
                        if (!isNaN(startDate.getTime())) {
                            const elapsedSec = Math.max(0, Math.floor((Date.now() - startDate.getTime()) / 1000));
                            const eh = Math.floor(elapsedSec / 3600);
                            const em = Math.floor((elapsedSec % 3600) / 60);
                            elapsedText = (eh > 0 ? `${eh}시간 ${em}분` : `${em}분`) + ' 방송중';
                        }
                    }
                    liveEmbedEl.innerHTML = `
                    <div class="profile-live-row">
                      <a class="profile-live-thumb-link" href="https://play.sooplive.co.kr/${attrUrlParam(tid)}" target="_blank" rel="noopener">
                        <img class="profile-live-thumb" src="https://liveimg.sooplive.co.kr/m/${attrUrlParam(broad.broad_no)}" alt="방송 화면">
                        <span class="profile-live-badge">LIVE</span>
                      </a>
                      <div class="profile-live-info">
                        <span class="profile-live-title"></span>
                        <span class="profile-live-viewer"></span>
                        <span class="profile-live-elapsed"></span>
                      </div>
                    </div>`;
                    liveEmbedEl.style.display = '';
                    fitTextToWidth(liveEmbedEl.querySelector('.profile-live-title'), broad.broad_title);
                    fitTextToWidth(liveEmbedEl.querySelector('.profile-live-viewer'), viewerText);
                    fitTextToWidth(liveEmbedEl.querySelector('.profile-live-elapsed'), elapsedText);
                } else {
                    liveEmbedEl.innerHTML = '';
                    liveEmbedEl.style.display = 'none';
                }
            };
            scheduleLiveStatus();
        }

        // 성별은 DB가 '남자'/'여자'로만 저장한다
        document.getElementById('profile-gender').textContent = member.gender || '-';
        document.getElementById('profile-birthdate').textContent = member.birthdate || '-';
        document.getElementById('profile-team').textContent = member.team || '-';
        document.getElementById('profile-role').textContent = member.role || '-';
        document.getElementById('profile-race').textContent = member.race || '-';
        document.getElementById('profile-tier').textContent = member.tier || '-';
        document.getElementById('profile-station-link').href =
            `https://www.sooplive.com/station/${encodeURIComponent(tid)}`;

        const fmt = n => (n ? n.toLocaleString('ko-KR') : '-');
        document.getElementById('profile-balloons').textContent = fmt(member.balloons);
        document.getElementById('profile-viewers').textContent = fmt(member.cumulative_viewers);
        document.getElementById('profile-broadcast').textContent = formatTime(member.broadcast_seconds) || '-';
        // 뷰어십이 없는 날은 null이다
        document.getElementById('profile-viewership').textContent = member.viewership_seconds
            ? metricDefs.viewership.format(member.viewership_seconds) + '시간'
            : '-';

        const sponsorEl = document.getElementById('profile-sponsor');
        const sGames = member.sponsor_games || 0;
        if (sGames > 0) {
            const sWins = member.sponsor_wins || 0;
            const sLosses = member.sponsor_losses || 0;
            const sRate = Math.round((sWins / sGames) * 100);
            sponsorEl.textContent = `${sWins}승 ${sLosses}패 (${sRate}%)`;
            sponsorEl.classList.remove('is-empty');
        } else {
            sponsorEl.textContent = '-';
            sponsorEl.classList.add('is-empty');
        }

        document.title = (member.nickname || tid) + ' 프로필';
        if (metaEl) {
            metaEl.innerHTML =
                '갱신 ' +
                escapeHtml(data.updated_at || '-') +
                ' · 출처 ' +
                [SOURCES.trackify, SOURCES.elo]
                    .map(
                        src =>
                            `<a href="${escapeHtml(src.url)}" target="_blank" rel="noopener" class="source-link">${escapeHtml(src.name)}</a>`
                    )
                    .join(', ');
        }
    }

    // 이미지 저장: 화면 밖 고정 폭 판(.png-sheet)에 옮겨 PNG로 내려받는다. html2canvas(약 200KB)는 처음 누를 때만 받는다.
    let html2canvasRequest = null;
    function loadHtml2canvas() {
        if (window.html2canvas) return Promise.resolve(window.html2canvas);
        if (!html2canvasRequest) {
            html2canvasRequest = new Promise((resolve, reject) => {
                const s = document.createElement('script');
                s.src = LOGO_PREFIX + 'html2canvas.min.js';
                s.onload = () => (window.html2canvas ? resolve(window.html2canvas) : reject(new Error('html2canvas')));
                s.onerror = () => {
                    html2canvasRequest = null;
                    s.remove();
                    reject(new Error('html2canvas.min.js를 받지 못했습니다'));
                };
                document.head.appendChild(s);
            });
        }
        return html2canvasRequest;
    }

    function buildPngSheet() {
        const grid = document.querySelector('#grid-container .grid');
        if (!grid || !grid.querySelector('.member-columns')) return null;
        const sheet = document.createElement('div');
        sheet.className = 'png-sheet' + (TARGET_TEAM ? ' single' : '');
        sheet.setAttribute('aria-hidden', 'true');
        const head = document.createElement('div');
        head.className = 'png-head';
        const title = document.createElement('div');
        title.className = 'png-title';
        const metricLabel = metricBtn ? currentMetricDef().label : '';
        // 팀 이름은 아래 카드에 있으므로 제목은 날짜·지표만
        title.textContent = [calBtn ? calBtn.textContent.trim() : currentDateStr, metricLabel]
            .filter(Boolean)
            .join(' · ');
        const meta = document.createElement('div');
        meta.className = 'png-meta';
        const metaEl = document.getElementById('top-meta-text');
        meta.textContent = metaEl ? metaEl.textContent : '';
        head.append(title, meta);
        const gridCopy = grid.cloneNode(true);
        gridCopy.querySelectorAll('.live-dot').forEach(el => el.remove());
        sheet.append(head, gridCopy);
        const legend = document.querySelector('.legend');
        if (legend) sheet.appendChild(legend.cloneNode(true));
        return sheet;
    }

    // 캔버스가 tabular-nums를 못 그려 숫자를 한 자씩 고정폭 칸으로 나눈다.
    // 칸 폭은 tabular 숫자 폭을 em으로 재서 폰에서 재도 PC 크기 판에 맞는다.
    function tabularizeDigits(sheet) {
        const widths = new Map();
        // DOM을 바꾸기 전에 글꼴 조건을 읽고, 같은 조건은 한 번만 잰다.
        const cells = Array.from(sheet.querySelectorAll('.member-value, .stat-value')).flatMap(el => {
            const text = el.textContent;
            if (!/\d/.test(text)) return [];
            const style = getComputedStyle(el);
            const key = JSON.stringify([
                style.fontFamily,
                style.fontWeight,
                style.fontStyle,
                style.fontStretch,
                style.fontFeatureSettings,
                style.fontVariationSettings,
                style.fontKerning,
                style.fontOpticalSizing,
                style.fontVariant,
                style.letterSpacing,
                style.wordSpacing,
                style.textTransform,
                style.writingMode,
                style.textOrientation,
                style.direction,
            ]);
            return [{ el, text, key }];
        });
        cells.forEach(({ el, key }) => {
            if (widths.has(key)) return;
            const probe = document.createElement('span');
            probe.style.cssText =
                'position:absolute;visibility:hidden;white-space:nowrap;font-size:100px;font-variant-numeric:tabular-nums';
            probe.textContent = '0000000000';
            el.appendChild(probe);
            widths.set(key, probe.getBoundingClientRect().width / 1000);
            probe.remove();
        });
        cells.forEach(({ el, text, key }) => {
            const digitEm = widths.get(key);
            if (digitEm > 0) el.style.setProperty('--digit-w', digitEm + 'em');
            el.innerHTML = Array.from(text)
                .map(ch => (/\d/.test(ch) ? `<span class="png-digit">${ch}</span>` : escapeHtml(ch)))
                .join('');
        });
    }

    async function savePng(btn) {
        const sheet = buildPngSheet();
        if (!sheet) return;
        btn.disabled = true;
        document.body.appendChild(sheet);
        try {
            const html2canvas = await loadHtml2canvas();
            if (document.fonts && document.fonts.ready) await document.fonts.ready;
            tabularizeDigits(sheet);
            const w = sheet.offsetWidth;
            const h = sheet.offsetHeight;
            // 최대 1,600만 화소·2배. 큰 시트는 1배 미만으로 줄인다.
            if (!w || !h) throw new Error('저장할 이미지 크기가 없습니다');
            const scale = Math.min(2, Math.sqrt(16000000 / (w * h)));
            const canvas = await html2canvas(sheet, {
                scale,
                useCORS: true,
                backgroundColor: getComputedStyle(document.body).backgroundColor,
                // 폰에서 눌러도 760px 판이 잘리지 않게 창 폭을 PC로 둔다
                windowWidth: 1280,
                windowHeight: 900,
                logging: false,
                onclone: doc => {
                    const copy = doc.querySelector('.png-sheet');
                    if (copy) copy.style.left = '0';
                },
            });
            const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
            if (!blob) throw new Error('PNG를 만들지 못했습니다');
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = ['시너지', TARGET_TEAM, currentDateStr, currentMetric].filter(Boolean).join('_') + '.png';
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(a.href), 10000);
        } catch (e) {
            console.error('이미지 저장 실패', e);
            alert('이미지를 만들지 못했습니다. 잠시 뒤 다시 시도해 주세요.');
        } finally {
            sheet.remove();
            btn.disabled = false;
        }
    }

    const pngBtn = document.getElementById('png-btn');
    if (pngBtn) pngBtn.addEventListener('click', () => savePng(pngBtn));

    // 날짜·지표 목록: 항목 선택·바깥 클릭·Esc로 닫고, 위아래 화살표로 옮긴다.
    // pick이 false를 돌려주면 닫지 않는다(날짜 목록의 달력)
    function setupMenu(button, menu, render, pick) {
        const close = () => {
            if (menu.hidden) return;
            menu.hidden = true;
            button.setAttribute('aria-expanded', 'false');
        };
        button.addEventListener('click', () => {
            if (!menu.hidden) return close();
            render();
            menu.hidden = false;
            button.setAttribute('aria-expanded', 'true');
            (menu.querySelector('[aria-current]') || menu.querySelector('button')).focus();
        });
        menu.addEventListener('click', e => {
            const item = e.target.closest('button');
            if (item && pick(item) !== false) close();
        });
        menu.addEventListener('keydown', e => {
            const items = [...menu.querySelectorAll('button:not(:disabled)')];
            const index = items.indexOf(document.activeElement);
            if (e.key === 'Escape') {
                close();
                button.focus();
            } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                items[(index + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
            }
        });
        // 다시 그리면 누른 버튼이 문서에서 빠지므로 눌렀을 때의 경로로 바깥인지 본다
        document.addEventListener('click', e => {
            const path = e.composedPath();
            if (!path.includes(button) && !path.includes(menu)) close();
        });
    }

    // 날짜 목록: 달마다 최신·최종 날짜와 '날짜 직접 고르기'(같은 자리에서 달력으로 바뀐다).
    const dateMenu = document.getElementById('date-menu');
    function renderDateMenu() {
        dateMenu.innerHTML =
            monthShortcuts()
                .map(d => {
                    const [y, m] = d.split('-');
                    const label = `${y.slice(-2)}년 ${m}월`;
                    const note = isMonthFinal(d) ? '최종' : '진행 중';
                    return `<button type="button" class="pick-menu-item" role="menuitem" data-date="${escapeHtml(d)}"${
                        d === currentDateStr ? ' aria-current="true"' : ''
                    }>${escapeHtml(label)}<span class="pick-menu-note">${note}</span></button>`;
                })
                .join('') +
            '<div class="pick-menu-sep" role="separator"></div>' +
            '<button type="button" class="pick-menu-item" role="menuitem" data-calendar>날짜 직접 고르기</button>';
    }
    let calendarMonth = '';
    function renderCalendar() {
        const [y, m] = calendarMonth.split('-').map(Number);
        const available = new Set(AVAILABLE_DATES.filter(d => d.startsWith(calendarMonth)));
        const oldestMonth = AVAILABLE_DATES[AVAILABLE_DATES.length - 1].slice(0, 7);
        const latestMonth = AVAILABLE_DATES[0].slice(0, 7);
        const blanks = new Date(y, m - 1, 1).getDay();
        const days = new Date(y, m, 0).getDate();
        let cells = '<span></span>'.repeat(blanks);
        for (let day = 1; day <= days; day++) {
            const d = `${calendarMonth}-${String(day).padStart(2, '0')}`;
            cells += available.has(d)
                ? `<button type="button" class="cal-day" data-date="${d}"${
                      d === currentDateStr ? ' aria-current="true"' : ''
                  }>${day}</button>`
                : `<span class="cal-day">${day}</span>`;
        }
        const nav = (step, label, disabled) =>
            `<button type="button" class="cal-nav" data-step="${step}" aria-label="${label}"${disabled ? ' disabled' : ''}></button>`;
        dateMenu.innerHTML =
            '<div class="cal">' +
            `<div class="cal-head">${nav(-1, '이전 달', calendarMonth <= oldestMonth)}` +
            `<span class="cal-title">${String(y).slice(-2)}년 ${String(m).padStart(2, '0')}월</span>` +
            `${nav(1, '다음 달', calendarMonth >= latestMonth)}</div>` +
            '<div class="cal-grid">' +
            ['일', '월', '화', '수', '목', '금', '토'].map(w => `<span class="cal-weekday">${w}</span>`).join('') +
            cells +
            '</div></div>';
    }
    if (calBtn && dateMenu) {
        setupMenu(
            calBtn,
            dateMenu,
            () => {
                calendarMonth = '';
                renderDateMenu();
            },
            item => {
                // '날짜 직접 고르기'는 지금 날짜의 달, 이전·다음 달 버튼은 옮긴 달의 달력(목록은 열어 둔다)
                const step = Number(item.dataset.step || 0);
                if (step || item.hasAttribute('data-calendar')) {
                    const [y, m] = (calendarMonth || currentDateStr).split('-').map(Number);
                    const month = new Date(y, m - 1 + step, 1);
                    calendarMonth = `${month.getFullYear()}-${String(month.getMonth() + 1).padStart(2, '0')}`;
                    renderCalendar();
                    (
                        dateMenu.querySelector(
                            step ? `.cal-nav[data-step="${step}"]:not(:disabled)` : '.cal-day[aria-current]'
                        ) || dateMenu.querySelector('button:not(:disabled)')
                    ).focus();
                    return false;
                }
                if (item.dataset.date && item.dataset.date !== currentDateStr) {
                    currentDateStr = item.dataset.date;
                    applyData();
                }
            }
        );
    }

    if (playerSearch) {
        playerSearch.addEventListener('input', renderPlayerSearch);
        playerSearch.addEventListener('focus', () => {
            loadSearchPlayers();
            renderPlayerSearch();
        });
        playerSearch.addEventListener('keydown', e => {
            if (e.key === 'Escape') closePlayerSearch();
            if (e.isComposing || playerSearchResults.hidden) return;
            const first = playerSearchResults.querySelector('a');
            if (first && (e.key === 'ArrowDown' || e.key === 'Enter')) {
                e.preventDefault();
                if (e.key === 'Enter') first.click();
                else first.focus();
            }
        });
        playerSearchResults.addEventListener('keydown', e => {
            const links = [...playerSearchResults.querySelectorAll('a')];
            const index = links.indexOf(document.activeElement);
            if (e.key === 'Escape') {
                playerSearch.focus();
                closePlayerSearch();
            } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                const next = index + (e.key === 'ArrowDown' ? 1 : -1);
                if (next < 0) playerSearch.focus();
                else if (links[next]) links[next].focus();
            }
        });
        document.addEventListener('click', e => {
            if (!playerSearch.closest('.player-search-box').contains(e.target)) closePlayerSearch();
        });
    }

    // 뷰어십은 값이 있을 때만(syncViewershipMetric)
    const metricMenu = document.getElementById('metric-menu');
    if (metricBtn && metricMenu) {
        setupMenu(
            metricBtn,
            metricMenu,
            () => {
                metricMenu.innerHTML = Object.keys(metricDefs)
                    .filter(key => key !== 'viewership' || viewershipAvailable)
                    .map(
                        key =>
                            `<button type="button" class="pick-menu-item" role="menuitem" data-metric="${escapeHtml(key)}"${
                                key === currentMetric ? ' aria-current="true"' : ''
                            }>${escapeHtml(metricDefs[key].label)}</button>`
                    )
                    .join('');
            },
            item => {
                const key = item.dataset.metric;
                if (!ownGet(metricDefs, key) || key === currentMetric) return;
                currentMetric = key;
                updateMetricChrome();
                applyData();
            }
        );
    }
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && !fetchedData.get(currentDateStr)) applyData();
    });
})();
