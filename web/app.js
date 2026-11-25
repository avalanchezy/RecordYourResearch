/* 论文投稿追踪系统 · 前端（Vue 3 本地构建，无外部依赖） */
const { createApp } = Vue;

// ---------- 界面语言（中文 / English，一键切换） ----------
// 中文原文就是翻译键：模板里用 $t(中文原文)，脚本里用 tt(中文原文)；英文在 i18n.en.js。
// 服务端返回的提示（错误、提醒、阶段名等）也走 tt()：先查词典，再按 i18n.en.js 里的模式翻译。
// 用户自己的数据（标题、作者、期刊名、备注……）从不经过 tt()，不会被翻译。
const I18N_EN = (window.RYR_I18N_EN && window.RYR_I18N_EN.dict) || {};
const I18N_PATTERNS = ((window.RYR_I18N_EN && window.RYR_I18N_EN.patterns) || [])
  .map(([re, rep]) => [new RegExp(re), rep]);
const HAN_RE = /[\u4e00-\u9fff]/;
const i18nState = Vue.reactive({ lang: (() => {
  try { const v = localStorage.getItem('ryr.lang'); if (v === 'zh' || v === 'en') return v; } catch {}
  return /^zh/i.test(navigator.language || '') ? 'zh' : 'en';
})() });
function trEn(s) {
  if (typeof s !== 'string' || !HAN_RE.test(s)) return s;
  if (Object.prototype.hasOwnProperty.call(I18N_EN, s)) return I18N_EN[s];
  const k = s.trim();
  if (k !== s && Object.prototype.hasOwnProperty.call(I18N_EN, k)) return s.replace(k, I18N_EN[k]);
  for (const [re, rep] of I18N_PATTERNS) {
    const m = re.exec(s);
    if (m) return rep.replace(/\$(\d)/g, (_, i) => trEn(m[+i] || ''));
  }
  // 组合文字逐段翻：「A · B」「A；B」「A、B」
  if (s.includes(' · ')) return s.split(' · ').map(trEn).join(' · ');
  if (s.includes('；')) return s.split('；').map(trEn).join('; ');
  if (s.includes('、')) return s.split('、').map(trEn).join(', ');
  return s;
}
function tt(s, args) {
  let out = i18nState.lang === 'en' ? trEn(s) : s;
  if (args && typeof out === 'string') {
    out = out.replace(/\{(\d+)\}/g, (m, i) => (args[i] === undefined || args[i] === null) ? '' : String(args[i]));
  }
  return out;
}

// 本地日期 YYYY-MM-DD（toISOString 是 UTC，东八区早上 8 点前会得到昨天）
function localDate(d = new Date()) {
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function localDateTime(d = new Date()) {
  const p = n => String(n).padStart(2, '0');
  return `${localDate(d)}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
// 'YYYY-MM-DD HH:MM[:SS]' / 'YYYY-MM-DD' → Date（本地时区；各浏览器一致）
function parseTs(s) {
  const m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?/);
  if (!m) return null;
  return new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
}
// 网址路由：#/overview、#/list?cat=&stage=&sort=&q=&sub=、#/new、#/query（sub = 打开的投稿抽屉）
const PAGES = ['overview', 'list', 'new', 'query'];
const SORTS = ['updated', 'venue', 'ddl', 'revise', 'days'];
function parseHash(hash) {
  const raw = String(hash || '').replace(/^#\/?/, '');
  const qi = raw.indexOf('?');
  const path = (qi >= 0 ? raw.slice(0, qi) : raw).replace(/\/+$/, '');
  return { page: PAGES.includes(path) ? path : 'overview',
           params: new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : '') };
}
function lsGet(k, d) { try { const v = JSON.parse(localStorage.getItem(k) || 'null'); return v ?? d; } catch { return d; } }
function lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} }
const emptyResub = () => ({
  open: false, saving: false, relation: '转投', title: '', submitted_at: localDate(), manuscript_number: '',
  copy_pdf: true, similar: [], similarBase: null, suggests: [], suggestOpen: false, focused: false,
  venue: { name: '', type: 'journal', year: '', tracker: 'manual', journal_acronym: '', acronym: '' },
});
const emptyRelForm = () => ({ open: false, q: '', results: [], target: null, relation: '转投', note: '' });
const emptyPasteForm = () => ({ open: false, text: '', parsed: null, loading: false, saving: false });
const newWizard = () => ({
  step: 1, tracker: 'manual', autoTracker: false, trackerHint: '', venueIdTouched: false,
  venueFocused: false, creating: false,
  venue: { name: '', type: 'journal', acronym: '', publisher: '', tracker: 'manual',
           venue_id: '', journal_acronym: '', year: '', ddl: '' },
  dragging: false, parsing: false, draft: null,
  rankShown: false, rankCard: null, rankProbeName: '',
  suggests: [], suggestOpen: false, _venDeb: null,
  form: { title: '', authorsText: '', abstract: '', keywordsText: '',
          submitted_at: localDate(), initial_stage: 'submitted', manuscript_number: '',
          corresponding_pinyin: '', openreview_forum: '', revise_due: '', notes: '' },
});

const app = createApp({
  data() {
    return {
      page: 'overview',
      clock: '',
      stages: {},
      categories: {},
      commonVenues: [],
      overview: {},
      recentEvents: [],
      subs: [],
      catCounts: {},
      catTabs: [
        { key: 'invest', label: '在投在审' },
        { key: 'accepted', label: '已录用' },
        { key: 'rejected', label: '已拒稿' },
        { key: 'withdrawn', label: '已撤稿' },
        { key: 'archived', label: '归档' },
        { key: '', label: '全部' },
        { key: 'trash', label: '回收站' },
      ],
      listFilter: { stage: '', q: '', category: 'invest', sort: 'updated' },
      listLoading: false,

      // 关联表单
      relForm: emptyRelForm(),

      // 详情
      showDetail: false,
      detail: null,
      editMode: false,
      editForm: {},
      editSuggests: [], editSuggestOpen: false,
      updateEvent: { open: false, stage: 'review', raw: '', observed_at: '', revise_due: '' },
      pasteForm: emptyPasteForm(),
      sysImport: { text: '', parsed: null, loading: false, saving: false },
      rawStatusOptions: ['Submitted to Journal', 'Technical Check in Progress', 'With Editor',
        'Reviewer Invited', 'Under Review', 'Required Reviews Complete',
        'Decision in Process', 'Revise - Major', 'Revise - Minor', 'Revision Submitted to Journal',
        'Accept', 'Reject', 'Desk Reject', 'Withdrawn', 'In Production'],

      // 向导（先丢 PDF → 缺啥补啥）
      wizard: newWizard(),

      // 分级速查
      rankQ: '', rankResult: null,

      // 渠道查询页
      pollingOR: false,
      pollResults: [],
      journals: [], journalsLoading: false,
      // 实验功能开关 / 分级数据集 / 版本信息（来自 /api/stages）
      features: { elsevier_query: false },
      demoMode: false,          // python run.py --demo：虚构数据
      rankDatasets: {},
      rankEditions: {},
      rankOutdated: false,
      secretStorage: '',
      appVersion: '', repoUrl: '',
      pollLogs: [],

      // OpenReview 账号
      orAccount: null,
      orForm: { email: '', secret: '' },
      binding: false, syncing: false,
      syncResult: null,

      // 提示条（可叠放，带「撤销」）/ 应用内确认框 / 抽屉「更多」菜单
      toasts: [],
      dialog: null,
      moreOpen: false,

      // 变化区（自上次查看以来）+ 桌面通知
      changes: { unread: 0, total: 0, changes: [], pending: [], pending_dismissed: 0,
                 now: '', seen_at: '', last_sync: null, account: null },
      changesExpanded: false, pendingExpanded: false,
      notifyPerm: typeof Notification === 'undefined' ? 'unsupported' : Notification.permission,

      // 抽屉布局
      tlExpanded: false,
      infoOpen: lsGet('pt.infoOpen', true) !== false,
      abstractOpen: false,
      resubForm: emptyResub(),

      // 备份
      backups: null, backingUp: false,
    };
  },

  computed: {
    lang() { return i18nState.lang; },
    stageOrder() {
      return Object.fromEntries(Object.entries(this.stages).sort((a, b) => a[1].order - b[1].order));
    },
    // 二级阶段筛选：只列出当前一级分类里的阶段（全部 = 全部阶段；单阶段分类不显示）
    filterStages() {
      const cat = this.listFilter.category;
      if (cat === '') return Object.keys(this.stageOrder);
      const sts = this.categories[cat] || [];
      return sts.length > 1 ? sts : [];
    },
    // 时间线：最新在上（默认显示最近 5 条）
    timelineDesc() { return this.detail ? [...(this.detail.events || [])].reverse() : []; },
    timelineShown() { return this.tlExpanded ? this.timelineDesc : this.timelineDesc.slice(0, 5); },
    latestEvent() { return this.timelineDesc[0] || null; },
    isTrash() { return this.listFilter.category === 'trash'; },
    inboxHas() { return !!(this.changes.total || (this.changes.pending || []).length); },
    // 变化区默认只显示最近 5 条变化 + 3 篇待建档，免得把「提醒」挤到下面
    changesShown() { const a = this.changes.changes || []; return this.changesExpanded ? a : a.slice(0, 5); },
    pendingShown() { const a = this.changes.pending || []; return this.pendingExpanded ? a : a.slice(0, 3); },
    // 自动同步出问题（解不开密码 / 上次同步失败）：总览顶部提示
    syncProblem() {
      const a = this.changes.account, s = this.changes.last_sync;
      if (a && a.needs_rebind) return { kind: 'rebind', msg: tt('自动同步已停止：本机解不开保存的 OpenReview 密码（数据库可能是从别的电脑或别的 Windows 用户复制来的），需要重新绑定账号。') };
      if (a && a.bound && s && !s.ok) return { kind: 'fail', msg: tt('上次账号同步失败（{0}）：{1}', [this.relTime(s.created_at), this.short(s.error || '', 90)]) };
      return null;
    },
    // 渠道查询页的同步结果：已忽略的不再列出
    syncDiscovered() { return ((this.syncResult && this.syncResult.discovered) || []).filter(d => !d._dismissed); },
    syncDismissedCount() { return ((this.syncResult && this.syncResult.discovered) || []).filter(d => d._dismissed).length; },
  },

  watch: {
    page(v) { this.loadPage(v); },
    'changes.unread'(n) { document.title = (n > 0 ? `(${n > 99 ? '99+' : n}) ` : '') + tt('论文投稿追踪系统'); },
    infoOpen(v) { lsSet('pt.infoOpen', v); },
    // 抽屉打开时：焦点移进抽屉、背后的页面不可聚焦（inert）；关上后焦点回到打开它的地方
    showDetail(v) {
      document.querySelectorAll('#app > header, #app > main, #app > footer').forEach(el => {
        if (v) el.setAttribute('inert', ''); else el.removeAttribute('inert');
      });
      if (v) {
        this.$nextTick(() => { try { this.$refs.drawerClose && this.$refs.drawerClose.focus({ preventScroll: true }); } catch {} });
      } else {
        const o = this._opener; this._opener = null;
        if (o && o.focus && document.contains(o)) this.$nextTick(() => { try { o.focus({ preventScroll: true }); } catch {} });
      }
    },
    // 向导：OpenReview 渠道下按「会议 + 年份」自动组装 venue id（用户手改过就不再覆盖）
    'wizard.venue.name'() { this.autoVenueId(); },
    'wizard.venue.year'() { this.autoVenueId(); },
    'wizard.tracker'() { this.autoVenueId(); },
  },

  async mounted() {
    try {
      const r = await this.api('/api/stages');
      this.stages = r.stages || {};
      this.categories = r.categories || {};
      this.commonVenues = r.common_venues || [];
      this.features = r.features || { elsevier_query: false };
      this.rankDatasets = r.rank_datasets || {};
      this.rankEditions = r.rank_editions || {};
      this.rankOutdated = !!r.rank_outdated;
      this.secretStorage = r.secret_storage || '';
      this.appVersion = r.version || '';
      this.demoMode = !!r.demo;
      this.repoUrl = r.repo || '';
    } catch {}
    this.applyLang();
    this.tick(); setInterval(this.tick, 1000 * 30);
    const lf = lsGet('pt.list', null);          // 上次的列表分类 / 排序（回收站不记）
    if (lf && typeof lf === 'object') {
      if (typeof lf.category === 'string' && lf.category !== 'trash' && this.catTabs.some(t => t.key === lf.category))
        this.listFilter.category = lf.category;
      if (SORTS.includes(lf.sort)) this.listFilter.sort = lf.sort;
    }
    window.addEventListener('popstate', () => this.applyRoute());
    window.addEventListener('hashchange', () => this.applyRoute());
    document.addEventListener('keydown', e => this.onGlobalKey(e));
    document.addEventListener('click', () => { this.moreOpen = false; });
    window.addEventListener('beforeunload', e => {
      if (this.isEditDirty()) { e.preventDefault(); e.returnValue = ''; }
    });
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') this.loadChanges();
    });
    try {        // 用户在网站设置里改了通知权限：跟着更新（不用刷新页面）
      if (navigator.permissions && navigator.permissions.query) {
        navigator.permissions.query({ name: 'notifications' }).then(st => {
          st.onchange = () => { this.notifyPerm = typeof Notification === 'undefined' ? 'unsupported' : Notification.permission; };
        }).catch(() => {});
      }
    } catch {}
    await this.applyRoute(true);
    await this.loadChanges();
    setInterval(() => this.loadChanges(), 5 * 60 * 1000);   // 每 5 分钟看一次有没有新变化
  },

  methods: {
    // ---------- 基础 ----------
    async api(url, opts = {}) {
      try {
        const r = await fetch(url, opts);
        if (!r.ok) {
          let msg = `HTTP ${r.status}`;
          try {
            const j = await r.json();
            if (typeof j.detail === 'string') msg = j.detail;
            else if (Array.isArray(j.detail))      // FastAPI 422：字段校验错误数组
              msg = j.detail.map(x => `${(x.loc || []).slice(1).join('.') || tt('参数')}：${x.msg}`).join('；');
          } catch {}
          throw new Error(msg);
        }
        return await r.json();
      } catch (e) {
        if (!opts.silent) this.$toast(tt('请求失败：{0}', [e.message]), 'bad');
        throw e;
      }
    },
    jsonOpts(method, body) {
      return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    },
    // 提示条：最多叠 3 条；带按钮（撤销 / 查看）的停留 7 秒，鼠标停在上面或键盘聚焦时不消失
    // 「撤销」只保留最新一条（避免点到旧的撤销）；普通提示不会挤掉撤销
    $toast(msg, type = 'ok', opts = {}) {
      const id = (this._toastSeq = (this._toastSeq || 0) + 1);
      const action = opts.action || null, undo = !!opts.undo;
      this.toasts = this.toasts.filter(t => (undo ? !t.undo : (t.action || t.msg !== msg)));
      this.toasts.push({ id, msg, type, action, undo });
      while (this.toasts.length > 3) {
        const i = this.toasts.findIndex(t => !t.undo);
        this.toasts.splice(i >= 0 ? i : 0, 1);
      }
      this._toastTimer(id, action ? 7000 : (type === 'ok' ? 3200 : 5200));
      return id;
    },
    _toastTimer(id, ms) {
      this._toastTimers = this._toastTimers || {};
      clearTimeout(this._toastTimers[id]);
      this._toastTimers[id] = setTimeout(() => this.dismissToast(id), ms);
    },
    holdToast(t) { if (this._toastTimers) clearTimeout(this._toastTimers[t.id]); },
    releaseToast(t) { if (this.toasts.some(x => x.id === t.id)) this._toastTimer(t.id, t.action ? 4000 : 2000); },
    dismissToast(id) {
      this.toasts = this.toasts.filter(t => t.id !== id);
      if (this._toastTimers) { clearTimeout(this._toastTimers[id]); delete this._toastTimers[id]; }
    },
    async runToastAction(t) {
      this.dismissToast(t.id);
      if (!t.action || t._ran) return;
      t._ran = true;
      try { await t.action.fn(); } catch {}
    },
    // 应用内确认框（代替浏览器 confirm）：返回 Promise<boolean>
    // 危险操作的确认框默认聚焦「取消」（一下回车不会误删）；Tab 只在框内切换；关掉后焦点回到原处
    confirmDialog(o) {
      if (this.dialog) this.dialog.resolve(false);
      else this._dlgReturn = document.activeElement;
      return new Promise(resolve => {
        this.dialog = { title: o.title || '', message: o.message || '', okText: o.okText || tt('确定'),
                        cancelText: o.cancelText === undefined ? tt('取消') : o.cancelText, danger: !!o.danger, resolve };
        this.$nextTick(() => {
          const el = (o.danger && this.$refs.dialogCancel) || this.$refs.dialogOk;
          try { el && el.focus(); } catch {}
        });
      });
    },
    closeDialog(v) {
      const d = this.dialog; this.dialog = null;
      if (d) d.resolve(!!v);
      const r = this._dlgReturn; this._dlgReturn = null;
      if (r && r.focus && document.contains(r)) this.$nextTick(() => { try { r.focus({ preventScroll: true }); } catch {} });
    },
    dialogKey(e) {
      if (e.key !== 'Tab') return;
      const btns = [...e.currentTarget.querySelectorAll('button')];
      if (!btns.length) return;
      e.preventDefault();
      const i = btns.indexOf(document.activeElement);
      btns[(i + (e.shiftKey ? -1 : 1) + btns.length) % btns.length].focus();
    },
    toggleMore(e) {
      this.moreOpen = !this.moreOpen;
      if (this.moreOpen && e && e.detail === 0) {      // 键盘打开：焦点给第一项
        this.$nextTick(() => { const f = document.querySelector('.more-menu button, .more-menu a'); if (f) f.focus(); });
      }
    },
    onMoreFocusOut(e) {
      const wrap = e.currentTarget;
      if (this.moreOpen && !(e.relatedTarget && wrap.contains(e.relatedTarget))) this.moreOpen = false;
    },
    // Esc 逐层关闭：确认框 → 菜单 → 抽屉里打开的表单 → 编辑（有改动先问）→ 抽屉
    onGlobalKey(e) {
      if (e.key !== 'Escape') return;
      if (this.dialog) { e.preventDefault(); this.closeDialog(false); return; }
      if (this.moreOpen) {
        this.moreOpen = false;
        try { this.$refs.moreBtn && this.$refs.moreBtn.focus(); } catch {}
        return;
      }
      if (!this.showDetail || e.defaultPrevented) return;
      if (this.resubForm.open) { this.resubForm.open = false; return; }
      if (this.pasteForm.open) { this.pasteForm.open = false; return; }
      if (this.updateEvent.open) { this.updateEvent.open = false; return; }
      if (this.relForm.open) { this.relForm.open = false; return; }
      if (this.editMode) { this.cancelEdit(); return; }
      this.closeDetail();
    },
    // 输入框里按 Esc：先收起联想下拉（不连带关掉抽屉）
    escSuggest(e, which) {
      if (which === 'edit' && this.editSuggestOpen) { this.editSuggestOpen = false; e.preventDefault(); }
      else if (which === 'resub' && this.resubForm.suggestOpen) { this.resubForm.suggestOpen = false; e.preventDefault(); }
      else if (which === 'rel' && this.relForm.results.length) { this.relForm.results = []; e.preventDefault(); }
    },
    tick() {
      this.clock = new Date().toLocaleString(this.lang === 'en' ? 'en-GB' : 'zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit',
                                                        hour: '2-digit', minute: '2-digit', hour12: false });
    },
    nowLocal() { return localDateTime(); },
    todayStr() { return localDate(); },
    short(s, n) { return s && s.length > n ? s.slice(0, n) + '…' : (s || ''); },
    fmtTs(s) { return String(s || '').replace('T', ' ').slice(0, 16); },
    fieldLabel(k) {
      return { title: tt('标题'), authors: tt('作者'), abstract: tt('摘要'), keywords: tt('关键词'),
               number: tt('稿件编号'), pinyin: tt('通讯作者拼音') }[k] || k;
    },
    catCount(key) { return this.catCounts[key === '' ? 'all' : key] ?? 0; },
    fmtDate(s) { return String(s || '').slice(0, 10); },
    // 相对时间：刚刚 / N 分钟前 / N 小时前 / 昨天 HH:MM / MM-DD HH:MM
    relTime(s) {
      const t = parseTs(s);
      if (!t) return '';
      const mins = (Date.now() - t.getTime()) / 60000;
      if (mins < 1) return tt('刚刚');
      if (mins < 60) return tt('{0} 分钟前', [Math.floor(mins)]);
      const today = parseTs(localDate());
      if (t >= today) return tt('{0} 小时前', [Math.floor(mins / 60)]);
      if (t >= new Date(today.getTime() - 86400000)) return tt('昨天 {0}', [this.fmtTs(s).slice(11, 16)]);
      return this.fmtTs(s).slice(5, 16);
    },
    dueText(d) {
      const n = this.dueDays(d);
      if (isNaN(n)) return '';
      return n < 0 ? tt('已逾期 {0} 天', [-n]) : (n === 0 ? tt('今天截止') : tt('剩 {0} 天', [n]));
    },
    // DDL：已过的灰显（多为投稿截止，已经投了就不用急）；7 天内橙色
    ddlClass(d) { const n = this.dueDays(d); return isNaN(n) ? '' : (n < 0 ? 'past' : (n <= 7 ? 'warn' : '')); },
    ddlText(d) {
      const n = this.dueDays(d);
      return isNaN(n) || n < 0 ? `DDL ${d}` : (n === 0 ? tt('DDL 今天') : tt('DDL 剩 {0} 天', [n]));
    },
    trashLeft(s) {
      const t = parseTs(s && s.deleted_at);
      if (!t) return '';
      const left = 30 - Math.floor((Date.now() - t.getTime()) / 86400000);
      return left > 0 ? tt('{0} 天后自动彻底删除', [left]) : tt('即将自动彻底删除');
    },
    safeUrl(u) { return /^https?:\/\//i.test(String(u || '')) ? String(u) : ''; },
    // 原始状态里真正变化的部分（OpenReview 形如「NeurIPS 2025 Conference Submission · 4 reviews released」）
    rawTail(s) { const t = String(s || ''); const i = t.lastIndexOf(' · '); return i >= 0 ? t.slice(i + 3) : t; },
    officialUrl(d) { return this.safeUrl(d.openreview_url) || this.safeUrl(d.venue_submit_url); },
    officialLabel(d) { return this.safeUrl(d.openreview_url) ? tt('OpenReview 稿件页') : tt('投稿系统'); },
    fmtSize(n) { return n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round((n || 0) / 1024)) + ' KB'; },
    catOfStage(k) { return Object.keys(this.categories).find(c => (this.categories[c] || []).includes(k)) ?? ''; },

    // 一键切换界面语言，记住选择
    toggleLang() {
      i18nState.lang = i18nState.lang === 'en' ? 'zh' : 'en';
      try { localStorage.setItem('ryr.lang', i18nState.lang); } catch {}
      this.applyLang();
      this.tick && this.tick();
    },
    applyLang() {
      document.documentElement.lang = i18nState.lang === 'en' ? 'en' : 'zh-CN';
      const n = this.changes ? this.changes.unread : 0;
      document.title = (n > 0 ? `(${n > 99 ? '99+' : n}) ` : '') + tt('论文投稿追踪系统');
    },
    stageLabel(k) { return tt((this.stages[k] || {}).label || k || '—'); },
    stageColor(k) { return (this.stages[k] || {}).color || '#64748b'; },
    chipStyle(k) { return { background: this.stageColor(k) }; },
    trackerLabel(t) { return { openreview: 'OpenReview', elsevier: tt('爱思唯尔'), manual: tt('手动') }[t] || t; },
    srcLabel(s) { return { manual: tt('手动'), openreview: tt('自动·OR'), elsevier: tt('半自动·爱思唯尔'), paste: tt('粘贴·投稿系统') }[s] || s; },
    // 官方页显示的「旧值→新值」：发出邀请 4→5、接受 2→3……（来自 payload.reviewers_prev）
    rvDelta(p) {
      const prev = p && p.reviewers_prev, cur = p && p.reviewers;
      if (!prev || !cur) return '';
      const names = { invited: tt('发出邀请'), accepted: tt('接受'), completed: tt('完成') };
      return ['invited', 'accepted', 'completed'].filter(k => prev[k] !== undefined && cur[k] !== undefined)
        .map(k => `${names[k]} ${prev[k]}→${this.rvNum(p, k)}`).join(i18nState.lang === 'en' ? ', ' : '、');
    },
    // 事后补录：记录时间比发生时间晚一天以上（例如补登历史状态、粘贴较早的官方结果）
    recordedLater(e) {
      const o = parseTs(e.observed_at), r = parseTs(e.recorded_at);
      return !!(o && r && r - o > 24 * 3600 * 1000);
    },
    // 审稿人计数：src 是解析预览或事件 payload；「5+」这种官方隐藏了具体人数的项带上 +
    rvNum(src, k) {
      const v = src && src.reviewers ? src.reviewers[k] : undefined;
      if (v === undefined || v === null) return '—';
      return (src.reviewers_at_least || []).includes(k) ? `${v}+` : `${v}`;
    },
    relLabel(r) {
      const dir = { 转投: [tt('转投为 →'), tt('← 转投自')], 扩写: [tt('扩写为 →'), tt('← 扩写自')] }[r.relation];
      return dir ? (r.direction === 'in' ? dir[1] : dir[0]) : tt(r.relation);
    },

    badgeClass(b) {
      if (b.scope === 'ccf') return 'rk-ccf ccf-' + (b.label.match(/CCF-(\w)/) || [])[1];
      if (b.scope === 'cas') return { 1: 'rk-cas rk1', 2: 'rk-cas rk2', 3: 'rk-cas rk3' }[b.label.match(/中科院(\d)/)?.[1]] || 'rk-cas';
      if (b.scope === 'xr') return 'rk-xr';
      if (b.scope === 'jcr') return 'rk-jcr';
      return 'rk-warn';
    },

    // ---------- 学术分级 ----------
    // 只去掉末尾的会议年份（2010–2099），「Periodontology 2000」这类刊名里的数字保留
    stripYear(name) { return (name || '').replace(/\s+20[1-9]\d\s*$/, '').trim(); },
    jcrLabel(j) {
      const q = String(j.quartile || '').trim(), f = String(j.if ?? '').trim();
      const qOk = q && !/^n\/?a$/i.test(q), fOk = f && !/^n\/?a$/i.test(f);
      return [qOk ? 'JCR ' + q : (fOk ? 'JCR' : ''), fOk ? 'IF ' + f : ''].filter(Boolean).join(' · ') || tt('JCR 收录');
    },
    xrWarnLabel(w) { return /^under\s*review$/i.test(String(w).trim()) ? tt('⚠ 新锐预警·观察中') : tt('⚠ 新锐预警：{0}', [w]); },
    async lookupWizardRank(venueType) {
      const w = this.wizard;
      const name = this.stripYear(w.venue.name || '');
      if (!name) { w.rankShown = false; w.rankCard = null; w.rankProbeName = ''; return null; }
      w.rankProbeName = name;
      w.rankShown = true;
      try {
        const r = await this.api('/api/rankings/lookup',
          { ...this.jsonOpts('POST', { name, venue_type: venueType || w.venue.type }), silent: true });
        if (w !== this.wizard || w.rankProbeName !== name) return null;   // 已有更新的查询，丢弃旧结果
        const card = { ccf: r.ccf || null, journal: r.journal || null };
        w.rankCard = (card.ccf || card.journal) ? card : null;
        return w.rankCard;
      } catch { w.rankCard = null; return null; }
    },
    async doRankSearch() {
      const q = this.rankQ.trim();
      if (!q) { this.rankResult = null; return; }
      try { this.rankResult = await this.api('/api/rankings?q=' + encodeURIComponent(q)); }
      catch {}
    },

    dueDays(d) {
      const due = parseTs(d);
      if (!due) return NaN;
      const today = parseTs(localDate());
      return Math.round((due - today) / 86400000);
    },
    dueClass(d) {
      if (!d) return '';
      const n = this.dueDays(d);
      return isNaN(n) ? '' : (n < 0 ? 'danger' : (n <= 3 ? 'warn' : ''));
    },
    // 时间线相邻两条事件的间隔（a 早、b 晚）
    gapDays(a, b) {
      const da = parseTs(a), db = parseTs(b);
      if (!da || !db) return '—';
      const d = (db - da) / 86400000;
      return d < 1 ? '<1' : Math.round(d);
    },
    barWidth(k) {
      const total = this.overview.total || 1;
      return Math.max(6, Math.round((this.overview.counts?.[k] || 0) / total * 100)) + '%';
    },

    // ---------- 总览 ----------
    async loadOverview() {
      try {
        this.overview = await this.api('/api/overview');
        this.recentEvents = await this.api('/api/recent-events');
      } catch {}
    },
    loadPage(v) {
      if (v === 'overview') { this.loadOverview(); this.loadChanges(); }
      if (v === 'list') this.loadSubs();
      if (v === 'query') { this.loadPollLogs(); this.loadORAccount(); this.loadBackups(); }
    },

    // ---------- 网址路由（前进/后退、刷新、收藏都回到同一视图） ----------
    buildHash(withSub = true) {
      const p = new URLSearchParams();
      if (this.page === 'list') {
        const f = this.listFilter;
        p.set('cat', f.category === '' ? 'all' : f.category);
        if (f.stage) p.set('stage', f.stage);
        if (f.sort && f.sort !== 'updated') p.set('sort', f.sort);
        if (f.q.trim()) p.set('q', f.q.trim());
      }
      if (withSub && this.showDetail && this.detail) p.set('sub', this.detail.id);
      const qs = p.toString();
      return '#/' + this.page + (qs ? '?' + qs : '');
    },
    setHash(mode = 'replace', state) {
      // 路由应用中 / 等待回退落地 / 离开确认框还开着时不改网址（否则会改写到错误的历史条目上）
      if (this._routing || this._navPending || this._guarding) return;
      const h = this.buildHash();
      if (location.hash === h) return;
      try {
        if (mode === 'push') history.pushState(state ?? null, '', h);
        else history.replaceState(state === undefined ? (this.showDetail ? history.state : null) : state, '', h);
      } catch {}
    },
    go(page) {
      if (this.page === page) { this.loadPage(page); return; }    // 再点一次 = 刷新
      this.page = page;
      this.setHash('push');
    },
    goList(category, stage = '') {
      const single = (this.categories[category] || []).length <= 1;
      this.listFilter = { ...this.listFilter, category, stage: single ? '' : stage, q: '' };
      if (this.page === 'list') this.loadSubs();
      else this.page = 'list';
      this.setHash('push');
    },
    async goInbox() {
      if (this.showDetail && !(await this.closeDetail())) return;     // 先关抽屉，否则变化区被挡住
      this.go('overview');
      await this.$nextTick();
      const el = document.getElementById('inbox') || document.getElementById('inbox-empty');
      if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },
    async applyRoute(initial = false) {
      if (this._applying) { this._reapply = true; return; }
      this._applying = true;
      try {
        do {
          this._reapply = false;
          await this._applyRouteOnce(initial);
          initial = false;
        } while (this._reapply);
      } finally {
        this._applying = false;
        if (this._navWaiter) this._navWaiter();     // closeDetail 在等这次回退落地
      }
    },
    async _applyRouteOnce(initial) {
      const { page, params } = parseHash(location.hash);
      let sid = parseInt(params.get('sub') || '0', 10) || 0;
      if (this._closingAll) {                 // ✕ 关闭后回退到的那一页若还带着 sub，一并关掉
        this._closingAll = false;
        sid = 0;
      }
      // 抽屉要关 / 换，但有未保存的内容：先问，不放弃就把网址恢复回去
      const drawerChanges = this.showDetail && (!sid || (this.detail && this.detail.id !== sid));
      if (drawerChanges && this.isEditDirty()) {
        this._guarding = true;
        let ok = false;
        try { ok = await this.leaveEditOk(); } finally { this._guarding = false; }
        if (!ok) {
          try { history.pushState({ drawer: ((history.state && history.state.drawer) || 0) + 1 }, '', this.buildHash()); } catch {}
          return;
        }
      }
      let opened = false;
      this._routing = true;
      try {
        if (page === 'list') {
          let f = { ...this.listFilter };
          if (params.has('cat')) {
            const c = params.get('cat');
            f = { category: c === 'all' ? '' : (this.catTabs.some(t => t.key === c) ? c : 'invest'),
                  stage: params.get('stage') || '', sort: params.get('sort') || 'updated', q: params.get('q') || '' };
            if (!SORTS.includes(f.sort)) f.sort = 'updated';
          }
          const changed = ['category', 'stage', 'sort', 'q'].some(k => f[k] !== this.listFilter[k]);
          this.listFilter = f;
          if (this.page !== 'list') this.page = 'list';
          else if (changed || initial) this.loadSubs();
        } else if (this.page !== page) {
          this.page = page;
        } else if (initial) {
          this.loadPage(page);
        }
        if (sid && !(this.showDetail && this.detail && this.detail.id === sid)) {
          opened = await this.openDetail(sid, { route: true });
        } else if (!sid && this.showDetail) {
          this.hideDetail();
        }
      } finally { this._routing = false; }
      // 深链/刷新打开的抽屉：在它下面垫一条不带抽屉的历史，✕ 和后退都回到列表（而不是离开应用）
      if (opened && !(history.state && history.state.drawer)) {
        try {
          history.replaceState(null, '', this.buildHash(false));
          history.pushState({ drawer: 1 }, '', this.buildHash());
        } catch {}
      }
      this.setHash('replace');   // 规范化网址（非法参数、未知页面、不存在的稿件）
    },

    // ---------- 列表（分类 + 排序） ----------
    async loadSubs() {
      this.setHash('replace');
      lsSet('pt.list', { category: this.listFilter.category, sort: this.listFilter.sort });
      const p = new URLSearchParams();
      if (this.listFilter.category) p.set('category', this.listFilter.category);
      if (this.listFilter.stage) p.set('stage', this.listFilter.stage);
      if (this.listFilter.q.trim()) p.set('q', this.listFilter.q.trim());
      if (this.listFilter.sort) p.set('sort', this.listFilter.sort);
      const seq = (this._listSeq = (this._listSeq || 0) + 1);
      this.listLoading = true;
      try {
        const rows = await this.api('/api/submissions?' + p);
        if (seq === this._listSeq) this.subs = rows;     // 丢弃过期的响应（快速切换分类/搜索）
      } catch {} finally {
        if (seq === this._listSeq) this.listLoading = false;
      }
      this.loadCatCounts();
    },
    async loadCatCounts() {
      try { this.catCounts = await this.api('/api/categories/counts', { silent: true }); } catch {}
    },
    setCategory(k) {
      this.listFilter.category = k;
      this.listFilter.stage = '';
      this.loadSubs();
    },
    debouncedLoadSubs() {
      clearTimeout(this._deb);
      this._deb = setTimeout(() => this.loadSubs(), 300);
    },
    toggleStage(k) {
      this.listFilter.stage = this.listFilter.stage === k ? '' : k;
      this.loadSubs();
    },
    refreshAll() {
      if (this.page === 'list') this.loadSubs(); else this.loadCatCounts();
      if (this.page === 'overview') { this.loadOverview(); this.loadChanges(); }
    },

    // ---------- 归档 / 相关投稿 ----------
    async toggleArchive(s, to = !s.archived, undo = false) {
      const id = s.id;
      try {
        await this.api(`/api/submissions/${id}/archive`, this.jsonOpts('POST', { archived: to }));
      } catch { return; }
      if (undo) this.$toast(tt('已撤销'));
      else this.$toast(to ? tt('已归档（不再出现在状态分类，可到「归档」页查看）') : tt('已取消归档'), 'ok',
        { undo: true, action: { label: '撤销', fn: () => this.toggleArchive({ id }, !to, true) } });
      this.refreshAll();
      if (this.detail && this.detail.id === id) await this.refreshDetail();
    },

    openRelForm() { this.relForm = { ...emptyRelForm(), open: true }; },
    searchRelated() {
      clearTimeout(this._relDeb);
      const f = this.relForm;
      f.target = null;                       // 重新输入 = 重新选择
      const q = f.q.trim();
      if (!q || q.length < 2) { f.results = []; return; }
      const sid = this.detail.id;
      this._relDeb = setTimeout(async () => {
        try {
          const rows = await this.api(
            `/api/submissions/search?q=${encodeURIComponent(q)}&exclude=${sid}`, { silent: true });
          if (this.detail && this.detail.id === sid && this.relForm.q.trim() === q) f.results = rows;
        } catch {}
      }, 250);
    },
    pickRelTarget(t) {
      const f = this.relForm;
      f.target = t; f.q = t.title; f.results = [];
    },
    async addRelated() {
      const f = this.relForm, sid = this.detail.id;
      if (!f.target) { this.$toast(tt('先搜索并选择要关联的稿件'), 'warn'); return; }
      try {
        const r = await this.api(`/api/submissions/${sid}/related`,
          this.jsonOpts('POST', { related_id: f.target.id, relation: f.relation, note: f.note }));
        this.$toast(`${r.updated ? tt('已更新关联') : tt('已关联')}：${tt(f.relation)} → #${f.target.id} ✓`);
      } catch { return; }
      this.relForm = emptyRelForm();
      await this.refreshDetail();
      this.refreshAll();
    },
    async removeRelated(r) {
      let res;
      try {
        res = await this.api(`/api/submissions/${this.detail.id}/related/${r.id}`, { method: 'DELETE' });
      } catch { return; }
      const link = res && res.link;
      this.$toast(tt('已解除与 #{0} 的关联', [r.id]), 'ok', link ? { undo: true, action: { label: '撤销', fn: async () => {
        try {
          await this.api(`/api/submissions/${link.submission_id}/related`, this.jsonOpts('POST',
            { related_id: link.related_id, relation: link.relation, note: link.note || '' }));
        } catch { return; }
        this.$toast(tt('已恢复关联'));
        await this.refreshDetail();
        this.refreshAll();
      } } } : {});
      await this.refreshDetail();
      this.refreshAll();
    },

    // ---------- 详情 ----------
    async openDetail(id, opts = {}) {
      const wasOpen = this.showDetail && !!this.detail;
      if (!opts.route && wasOpen && this.detail.id !== id && !(await this.leaveEditOk())) return false;
      let d;
      try { d = await this.api(`/api/submissions/${id}`); } catch { return false; }
      if (!wasOpen && !opts.route) {
        const a = document.activeElement;
        this._opener = a && a !== document.body ? a : null;
      }
      d._querying = false;
      this.detail = d;
      // 切换稿件时清空所有与上一篇相关的临时表单，避免把粘贴结果/关联写到别的稿件上
      this.editMode = false;
      this.pasteForm = emptyPasteForm();
      this.relForm = emptyRelForm();
      this.resubForm = emptyResub();
      this.moreOpen = false;
      this.tlExpanded = false;
      this.abstractOpen = false;
      this.updateEvent = { open: false, stage: d.current_stage, raw: '', observed_at: '',
                           revise_due: d.revise_due || '' };
      this.showDetail = true;
      // 历史里记下抽屉层数：只数屏幕上真的叠着的抽屉（关闭时按层数回退）
      if (!opts.route) this.setHash('push', { drawer: wasOpen ? ((history.state && history.state.drawer) || 0) + 1 : 1 });
      return true;
    },
    async refreshDetail() {
      if (!this.detail) return;
      try {
        const d = await this.api(`/api/submissions/${this.detail.id}`);
        d._querying = this.detail._querying;
        this.detail = d;
      } catch {}
    },
    // 关闭抽屉：按层数回退到打开抽屉前的那一页（与后退键一致），等回退落地后再返回
    async closeDetail(force = false) {
      if (!force && !(await this.leaveEditOk())) return false;
      this.hideDetail();
      const depth = (history.state && history.state.drawer) || 0;
      if (depth > 0) {
        this._closingAll = true;
        this._navPending = true;
        await new Promise(resolve => {
          let finished = false;
          const done = () => {
            if (finished) return;
            finished = true;
            clearTimeout(timer);
            if (this._navWaiter === done) this._navWaiter = null;
            this._navPending = false;
            this._closingAll = false;
            resolve();
          };
          const timer = setTimeout(done, 1000);
          this._navWaiter = done;
          try { history.go(-depth); } catch { done(); }
        });
      }
      this.setHash('replace', null);
      return true;
    },
    hideDetail() { this.showDetail = false; this.editMode = false; this.moreOpen = false; this.resubForm.open = false; },
    editDirty() {
      return !!(this.editMode && this.editOrig && JSON.stringify(this.editForm) !== JSON.stringify(this.editOrig));
    },
    // 抽屉里还没保存的内容（编辑信息 / 粘贴的官方结果 / 转投表单），离开抽屉前要先问
    dirtyWhat() {
      if (!this.showDetail) return '';
      if (this.editDirty()) return tt('「编辑信息」里的改动');
      if ((this.pasteForm.text || '').trim() && !this.pasteForm.saving) return tt('粘贴的官方结果');
      const r = this.resubForm;
      if (r.open && ((r.venue.name || '').trim() || (r.title || '').trim() || (r.manuscript_number || '').trim()))
        return tt('「转投」表单里填的内容');
      return '';
    },
    isEditDirty() { return !!this.dirtyWhat(); },
    async leaveEditOk() {
      const what = this.dirtyWhat();
      if (!what) return true;
      return this.confirmDialog({ title: tt('放弃未保存的内容？'), message: tt('{0}还没保存，离开后会丢失。', [what]),
                                  okText: tt('放弃'), cancelText: tt('继续编辑'), danger: true });
    },
    async cancelEdit() {
      if (this.editDirty() && !(await this.confirmDialog({ title: tt('放弃未保存的修改？'),
            message: tt('「编辑信息」里的改动还没保存，取消后会丢失。'), okText: tt('放弃修改'), cancelText: tt('继续编辑'), danger: true })))
        return;
      this.editMode = false;
    },
    // 遮罩：按下和松开都在遮罩上才算「点空白处关闭」（在抽屉里拖选文字松手到外面不会误关）
    onMaskDown(e) { this._maskDown = e.target === e.currentTarget; },
    onMaskClick(e) {
      const ok = this._maskDown && e.target === e.currentTarget;
      this._maskDown = false;
      if (ok) this.closeDetail();
    },
    togglePaste() {
      this.pasteForm.open = !this.pasteForm.open;
      if (this.pasteForm.open) { this.updateEvent.open = false; this.resubForm.open = false; }
    },
    openPdf(s) { window.open(`/api/submissions/${s.id}/pdf`, '_blank'); },

    // 每次打开「登记状态变化」都按当前稿件重新填表（不残留上一次的原始状态/时间）
    openEventForm() {
      const d = this.detail;
      const open = !this.updateEvent.open;
      this.updateEvent = { open, stage: d.current_stage, raw: '', observed_at: '', revise_due: d.revise_due || '' };
      if (open) { this.pasteForm.open = false; this.resubForm.open = false; }
    },
    async addEvent() {
      const e = this.updateEvent;
      if (!e.stage) return;
      const body = { stage: e.stage, raw: e.raw, observed_at: (e.observed_at || '').replace('T', ' ') };
      // 返修截止日只在填了时提交（留空 = 不改动已登记的截止日）
      if (e.stage === 'revise' && e.revise_due) body.revise_due = e.revise_due;
      let r;
      try {
        r = await this.api(`/api/submissions/${this.detail.id}/events`, this.jsonOpts('POST', body));
      } catch { return; }
      this.updateEvent = { open: false, stage: r.current_stage, raw: '', observed_at: '', revise_due: '' };
      if (r.backdated) this.$toast(tt('已补录到时间线（当前状态仍为「{0}」，时间线上有更晚的记录）', [this.stageLabel(r.current_stage)]), 'warn');
      else this.$toast(tt('状态变化已记入时间线 ✓'));
      await this.refreshDetail();
      this.refreshAll();
    },
    async deleteEvent(e) {
      const sid = this.detail.id;
      let res;
      try {
        res = await this.api(`/api/submissions/${sid}/events/${e.id}`, { method: 'DELETE' });
      } catch { return; }
      const ev = res && res.event;
      this.$toast(tt('已删除状态记录：{0} · {1}', [this.stageLabel(e.stage), this.fmtTs(e.observed_at)]), 'ok',
        ev ? { undo: true, action: { label: '撤销', fn: async () => {
          try { await this.api(`/api/submissions/${sid}/events/restore`, this.jsonOpts('POST', ev)); } catch { return; }
          this.$toast(tt('已恢复这条状态记录'));
          if (this.detail && this.detail.id === sid) await this.refreshDetail();
          this.refreshAll();
        } } } : {});
      await this.refreshDetail();
      this.refreshAll();
    },

    startEdit() {
      const d = this.detail;
      this.editOrig = null;
      this.editForm = {
        title: d.title, authorsText: d.authors.join(', '),
        abstract: d.abstract || '', keywordsText: (d.keywords || []).join(', '),
        venue_name: d.venue_base_name || '',
        venue_year: d.venue_year || '',
        venue_type: d.venue_type || 'journal',
        tracker: d.venue_tracker || 'manual',
        journal_acronym: d.venue_journal_acronym || '',
        openreview_venue_id: d.venue_openreview_id || '',
        ddl: d.venue_ddl || '', publisher: d.venue_publisher || '',
        corresponding_pinyin: d.corresponding_pinyin || '',
        manuscript_number: d.manuscript_number || '',
        submitted_at: d.submitted_at || '', revise_due: d.revise_due || '',
        openreview_forum: d.openreview_forum || '', notes: d.notes || '',
        submit_url: d.venue_submit_url_stored || '',
      };
      this.editOrig = JSON.parse(JSON.stringify(this.editForm));   // 打开编辑时的快照：只提交改动过的字段
      this.editSuggests = []; this.editSuggestOpen = false;
      this.pasteForm.open = false; this.updateEvent.open = false; this.resubForm.open = false;
      this.editMode = true;
    },
    async saveEdit() {
      const f = this.editForm, o = this.editOrig || {}, d = this.detail;
      if (!f.title.trim()) { this.$toast(tt('标题不能为空'), 'warn'); return; }
      if (!f.venue_name.trim()) { this.$toast(tt('会议/期刊名称不能为空'), 'warn'); return; }
      const split = s => s.split(/[,，;；]/).map(x => x.trim()).filter(Boolean);
      const val = v => (typeof v === 'string' ? v.trim() : String(v ?? '').trim());
      // 只提交相对打开编辑时改动过的字段：编辑期间别的操作（粘贴入档、OpenReview 回填 forum）写入的值不会被旧快照覆盖
      const sp = {};
      for (const k of ['title', 'abstract', 'corresponding_pinyin', 'manuscript_number',
                       'submitted_at', 'revise_due', 'openreview_forum', 'notes']) {
        if (val(f[k]) !== val(o[k])) sp[k] = val(f[k]);
      }
      if (val(f.authorsText) !== val(o.authorsText)) sp.authors = split(f.authorsText);
      if (val(f.keywordsText) !== val(o.keywordsText)) sp.keywords = split(f.keywordsText);
      // 会议/期刊信息（名称里带年份时后端会自动拆出年份）
      const vp = {};
      for (const [k, key] of [['venue_name', 'name'], ['venue_year', 'year'], ['venue_type', 'type'],
                              ['tracker', 'tracker'], ['journal_acronym', 'journal_acronym'],
                              ['openreview_venue_id', 'venue_id'], ['ddl', 'ddl'], ['publisher', 'publisher'],
                              ['submit_url', 'submit_url']]) {
        if (val(f[k]) !== val(o[k])) vp[key] = val(f[k]);
      }
      if (vp.year && !/^\d{4}$/.test(vp.year)) { this.$toast(tt('年份应为四位数字，如 2027'), 'warn'); return; }
      if (vp.submit_url && !/^https?:\/\//i.test(vp.submit_url)) {
        this.$toast(tt('投稿系统链接应以 http:// 或 https:// 开头'), 'warn'); return;
      }
      if (sp.submitted_at && sp.submitted_at > localDate()) { this.$toast(tt('投稿日期不能晚于今天'), 'warn'); return; }
      try {
        // 先改会议信息（校验更严），再改稿件字段：避免一半保存成功、一半失败
        if (Object.keys(vp).length) await this.api(`/api/submissions/${d.id}/venue`, this.jsonOpts('PATCH', vp));
        if (Object.keys(sp).length) await this.api(`/api/submissions/${d.id}`, this.jsonOpts('PATCH', sp));
      } catch {                // 保存失败：保留编辑状态，方便修改后重试；抽屉按已保存的内容刷新
        await this.refreshDetail();
        return;
      }
      this.editMode = false;
      this.$toast(tt('已保存 ✓'));
      await this.refreshDetail();
      this.refreshAll();
    },

    // ---------- 编辑模式的期刊联想 ----------
    onEditVenueInput() {
      const q = this.stripYear(this.editForm.venue_name || '');
      clearTimeout(this._editDeb);
      if (!q || q.length < 2) { this.editSuggests = []; this.editSuggestOpen = false; return; }
      this._editDeb = setTimeout(async () => {
        try {
          const r = await this.api('/api/rankings/suggest?q=' + encodeURIComponent(q), { silent: true });
          this.editSuggests = (r.items || []).filter(s => this.stripYear(s.name) !== q);
          this.editSuggestOpen = !!this._editFocused && this.editSuggests.length > 0;
        } catch {}
      }, 260);
    },
    setEditFocus(v) {
      this._editFocused = v;
      if (!v) { clearTimeout(this._editDeb); this.editSuggestOpen = false; }
    },
    pickEditSuggest(s) {
      this.editForm.venue_name = s.name;
      this.editSuggestOpen = false;
      if (s.ccf?.type) this.editForm.venue_type = s.ccf.type;
    },

    // 删除 = 移到回收站（可撤销；30 天后自动彻底删除）
    async trashSub(s) {
      const id = s.id, title = s.title;
      try { await this.api(`/api/submissions/${id}`, { method: 'DELETE' }); } catch { return; }
      if (this.detail && this.detail.id === id && this.showDetail) await this.closeDetail(true);
      this.$toast(tt('已移到回收站：《{0}》', [this.short(title, 24)]), 'ok',
                  { undo: true, action: { label: '撤销', fn: () => this.restoreSub({ id, title }, true) } });
      this.refreshAll();
    },
    async restoreSub(s, undo = false) {
      try { await this.api(`/api/submissions/${s.id}/restore`, { method: 'POST' }); } catch { return; }
      this.$toast(undo ? tt('已撤销，稿件已放回原处') : tt('已恢复：《{0}》', [this.short(s.title, 24)]));
      this.refreshAll();
      if (this.detail && this.detail.id === s.id) await this.refreshDetail();
    },
    async purgeSub(s) {
      const ok = await this.confirmDialog({
        title: tt('彻底删除这篇投稿？'),
        message: tt('《{0}》\n\nPDF 原件、状态时间线与关联会一并删除，无法恢复。', [this.short(s.title, 60)]),
        okText: tt('彻底删除'), danger: true });
      if (!ok) return;
      try { await this.api(`/api/submissions/${s.id}?purge=1`, { method: 'DELETE' }); } catch { return; }
      if (this.detail && this.detail.id === s.id && this.showDetail) await this.closeDetail(true);
      this.$toast(tt('已彻底删除'));
      this.refreshAll();
    },

    // ---------- 粘贴官方结果（自适应解析 → 自动入档） ----------
    async parsePaste() {
      const form = this.pasteForm, text = form.text, sid = this.detail.id;
      if (!text.trim()) { this.$toast(tt('先粘贴官方页内容'), 'warn'); return; }
      form.loading = true;
      try {
        const p = await this.api(`/api/submissions/${sid}/elsevier-paste`, this.jsonOpts('POST', { text }));
        // 解析期间改了文本或切换了稿件：这份结果已过期，丢弃
        if (this.pasteForm !== form || form.text !== text || this.detail?.id !== sid) return;
        p._text = text;                      // 记住预览的是哪段文本，确认时原样提交
        p._future = !!(p.observed_at && p.observed_at > localDate());
        form.parsed = p;
        if (p.warning) this.$toast(p.warning, 'bad');
        else if (!p.recognized) {
          this.$toast(tt('解析不到状态信息，请确认粘贴的是官方查询页（过码后的结果页）内容'), 'warn');
        } else {
          this.$toast(tt('解析完成 ✓ 核对后点「记入时间线」'));
        }
      } catch {} finally { form.loading = false; }
    },
    onPasteInput() { this.pasteForm.parsed = null; },   // 文本改了，旧预览作废，需重新解析
    async confirmPaste() {
      const p = this.pasteForm.parsed;
      if (!p || !p.recognized || p.warning) return;
      this.pasteForm.saving = true;
      try {
        const r = await this.api(`/api/submissions/${this.detail.id}/elsevier-paste/confirm`,
          this.jsonOpts('POST', { text: p._text }));
        if (r.written && r.backdated) {
          this.$toast(tt('已记入时间线：{0}（当前状态仍为「{1}」，时间线上有更晚的记录）', [this.stageLabel(r.stage), this.stageLabel(r.current_stage)]), 'warn');
        } else if (r.written) this.$toast(tt('已记入时间线：{0} ✓', [this.stageLabel(r.stage)]));
        else this.$toast(r.hint || tt('该状态已记录过，跳过重复写入'), 'warn');
        this.pasteForm = emptyPasteForm();
        await this.refreshDetail();
        this.refreshAll();
        this.loadChanges({ quiet: true });
      } catch {} finally { this.pasteForm.saving = false; }
    },

    // ---------- 投稿系统页面导入（用户自己登录后整页复制粘贴）----------
    async parseSysImport() {
      const f = this.sysImport, text = f.text;
      if (!text.trim()) { this.$toast(tt('先粘贴投稿系统页面的内容'), 'warn'); return; }
      f.loading = true;
      try {
        const p = await this.api('/api/import/system-paste', this.jsonOpts('POST', { text }));
        if (this.sysImport !== f || f.text !== text) return;       // 解析期间改了文本：结果作废
        p._text = text;
        for (const it of p.items) {
          // 按标题对上的默认不勾：请先看一眼对的是不是同一篇
          it._pick = (it.action === 'update' && it.match && it.match.by === 'number') || it.action === 'create';
          it._venue = it.journal || p.journal || '';
        }
        f.parsed = p;
        if (p.items.length) this.$toast(tt('认出 {0} 篇稿件，核对后点「导入选中」', [p.items.length]));
        else this.$toast(tt('没有认出稿件：请复制作者中心里列出稿件（含稿件编号）的页面'), 'warn');
      } catch {} finally { f.loading = false; }
    },
    onSysPaste() { setTimeout(() => this.parseSysImport(), 0); },
    sysPicks() {
      return (this.sysImport.parsed?.items || []).map((it, i) => ({ it, i })).filter(x => x.it._pick);
    },
    sysActionText(it) {
      return { update: tt('记入时间线'), same: tt('已是最新'), create: tt('新建'), unknown: tt('状态未识别'),
               skip: tt('草稿 / 已被取代，不导入'), no_title: tt('缺标题，不能新建'),
               ambiguous: tt('对上了多篇，请手动处理'), trashed: tt('在回收站里（先恢复再导入）') }[it.action] || '';
    },
    sysMatchText(it) {
      const m = it.match;
      if (!m) return '';
      return `《${this.short(m.title, 40)}》` + (m.venue ? ` · ${m.venue}` : '') + (m.by === 'title' ? tt(' · 按标题对上，请核对') : '');
    },
    async confirmSysImport() {
      const p = this.sysImport.parsed;
      if (!p) return;
      const picks = this.sysPicks().map(({ it, i }) => ({ index: i, action: it.action === 'create' ? 'create' : 'update',
                                                        venue_name: it.action === 'create' ? (it._venue || '').trim() : '' }));
      if (!picks.length) return;
      if (picks.some(x => x.action === 'create' && !x.venue_name)) {
        this.$toast(tt('新建的稿件需要期刊名：请在表格里补上'), 'warn'); return;
      }
      this.sysImport.saving = true;
      try {
        const r = await this.api('/api/import/system-paste/confirm', this.jsonOpts('POST', { text: p._text, picks }));
        this.$toast(tt('已记入 {0} 篇、新建 {1} 篇', [r.updated, r.created]) + (r.unchanged ? tt('，{0} 篇已是最新', [r.unchanged]) : '')
          + (r.backdated ? tt('；其中 {0} 篇时间线上有更晚的记录，当前状态没变', [r.backdated]) : '') + ' ✓', r.backdated ? 'warn' : '');
        this.sysImport = { text: '', parsed: null, loading: false, saving: false };
        this.refreshAll();
      } catch {} finally { this.sysImport.saving = false; }
    },
    async goSysImport() {
      if (this.showDetail && !(await this.closeDetail())) return;     // 有未保存的编辑时先问
      this.go('query');
      setTimeout(() => document.getElementById('sys-import')?.scrollIntoView({ behavior: 'smooth' }), 200);
    },

    // ---------- 渠道查询 ----------
    orResultToast(r) {
      if (r.ok) {
        const st = `${this.stageLabel(r.stage)}${r.raw ? '（' + r.raw + '）' : ''}`;
        this.$toast(r.changed ? tt('OpenReview 状态更新：{0}', [st]) : tt('OpenReview：{0}，与上次相同，未新增记录', [st]));
      } else this.$toast(r.error || tt('查询失败'), 'warn');
    },
    async queryOR(s) {
      if (s._querying) return;
      s._querying = true;
      try {
        const r = await this.api('/api/query/openreview', this.jsonOpts('POST', { submission_id: s.id }));
        this.orResultToast(r);
      } catch {} finally {
        s._querying = false;
        if (this.detail && this.detail.id === s.id) await this.refreshDetail();
        this.refreshAll();
        this.loadChanges({ quiet: true });
      }
    },
    async queryElsevier(s) {
      if (s._querying) return;
      s._querying = true;
      try {
        const r = await this.api('/api/query/elsevier', this.jsonOpts('POST', { submission_id: s.id }));
        if (r.ok) {
          this.$toast(tt('爱思唯尔：{0}（{1}）{2}', [this.stageLabel(r.stage), r.raw_zh || r.raw, r.changed ? '' : tt('，与上次相同')]));
        } else if (r.need_manual) {
          // 官方要滑块验证码（不能绕过）→ 只复制编号，其余用户自填
          const cr = r.credentials || {};
          const num = cr.manuscript_number || '';
          let didCopy = false;
          if (num) {
            try { await navigator.clipboard.writeText(num); didCopy = true; } catch {}
          }
          const go = await this.confirmDialog({
            title: tt('官方需要滑块验证码（按设计不绕过）'),
            message: tt('稿件编号：{0} {1}\n姓名拼音：{2}\n期刊缩写：{3}\n\n打开官方查询页 → 粘贴编号 → 填姓名 → 滑块过码查询。\n查到后整页复制，回到详情页「📋 粘贴官方结果」自动解析入档。', [num || '—', didCopy ? tt('（已复制，Ctrl+V 直接粘贴）') : '', cr.pinyin || '—', cr.journal_acronym || '—']),
            okText: tt('打开官方查询页'), cancelText: tt('稍后') });
          if (go) window.open(this.safeUrl(cr.official_web) || 'https://webapps.elsevier.cn/st-wechat/manuscript-query', '_blank', 'noopener');
        } else {
          this.$toast(r.error || tt('查询失败'), 'bad');
        }
      } catch {} finally {
        s._querying = false;
        if (this.detail && this.detail.id === s.id) await this.refreshDetail();
        this.refreshAll();
        this.loadChanges({ quiet: true });
      }
    },

    async pollAll() {
      this.pollingOR = true; this.pollResults = [];
      try {
        const r = await this.api('/api/poll/openreview', { method: 'POST' });
        if (r.busy || r.need_account) { this.$toast(r.error, 'warn'); return; }
        this.pollResults = r.results || [];
        this.$toast(r.polled ? tt('轮询完成：{0}/{1} 成功，{2} 篇状态有变化', [r.ok, r.polled, r.changed || 0])
                             : tt('没有需要轮询的 OpenReview 稿件（已归档/已拒/已撤的会跳过）'));
        this.loadPollLogs();
        this.loadCatCounts();
        this.loadChanges({ quiet: true });
      } catch {} finally { this.pollingOR = false; }
    },
    // 「爱思唯尔一键查稿」实验功能：默认关闭，开启前讲清楚风险
    async toggleElsevierQuery() {
      const on = !this.features.elsevier_query;
      if (on && !(await this.confirmDialog({
        title: tt('开启「爱思唯尔一键查稿」实验功能？'),
        message: tt('它会用你填的稿件编号 + 通讯作者拼音 + 期刊缩写，向爱思唯尔中国的稿件查询服务发请求。\n\n')
               + tt('· 非官方功能，与爱思唯尔无关，接口可能随时失效；\n')
               + tt('· 是否符合爱思唯尔网站使用条款请自行判断，风险自负；\n')
               + tt('· 只在你点「查」时请求，每篇稿件 24 小时最多一次；遇到验证码就停下，请到官方页面人工查询后用「粘贴官方结果」。'),
        okText: tt('我了解，开启'), cancelText: tt('先不开') }))) return;
      try {
        this.features = await this.api('/api/settings', this.jsonOpts('POST', { elsevier_query: on }));
      } catch { return; }
      this.$toast(on ? tt('已开启「爱思唯尔一键查稿」（实验）') : tt('已关闭「爱思唯尔一键查稿」'));
    },
    rankMissingHint() {
      const d = this.rankDatasets || {};
      if (this.rankOutdated) return tt('随项目附带的分级数据有更新；你的分级库里有自己导入的数据，所以没有自动重建。')
        + tt('运行 tools/build_rankings.py 并带上原来的 CSV（例如 --jcr 你的JCR.csv）即可合并，见 README「分级数据」。');
      if (!d.ccf) return tt('分级库不可用：运行 tools/build_rankings.py 重建（见 README「分级数据」）。');
      return (d.cas || d.xr) ? '' : tt('分级库里没有中科院分区 / 新锐分区：运行 tools/build_rankings.py 重建（见 README「分级数据」）。');
    },
    rankSourcesText() {
      const d = this.rankDatasets || {}, e = this.rankEditions || {};
      const ed = k => (e[k] ? ' ' + e[k] : '');
      return [tt('CCF 推荐目录') + ed('ccf'), d.cas ? tt('中科院分区') + ed('cas') : '', d.xr ? tt('新锐分区') + ed('xr') : '',
              d.jcr ? 'JCR' + ed('jcr') : '', tt('中科院预警名单')].filter(Boolean).join(' · ');
    },
    secretStorageText(s) {
      return { dpapi: tt('Windows 凭据保护：只有这台电脑上的当前 Windows 用户能解开'),
               keyring: tt('系统钥匙串：macOS 钥匙串 / Linux Secret Service') }[s] || '';
    },
    async loadJournals() {
      this.journalsLoading = true;
      try {
        const r = await this.api('/api/query/elsevier/journals');
        if (r.ok) { this.journals = r.journals || []; this.$toast(tt('取到 {0} 个期刊缩写', [this.journals.length])); }
        else this.$toast(r.error || tt('字典拉取失败'), 'warn');
      } catch {} finally { this.journalsLoading = false; }
    },
    async loadPollLogs() {
      try { this.pollLogs = await this.api('/api/poll-logs', { silent: true }); } catch {}
    },

    // ---------- OpenReview 账号 ----------
    async loadORAccount() {
      try {
        const list = await this.api('/api/accounts', { silent: true });
        this.orAccount = list.find(a => a.channel === 'openreview') || null;
      } catch {}
    },
    async bindORAccount() {
      if (!this.orForm.email.trim() || !this.orForm.secret) { this.$toast(tt('请填写邮箱与密码'), 'warn'); return; }
      this.binding = true;
      try {
        const r = await this.api('/api/accounts/openreview', this.jsonOpts('POST',
          { email: this.orForm.email.trim(), secret: this.orForm.secret }));
        this.orForm = { email: '', secret: '' };
        this.$toast(tt('绑定成功：{0}，名下 {1} 篇投稿 ✓', [r.profile.id, r.my_submissions]));
        await this.loadORAccount();
        this.syncMySubmissions();
      } catch {} finally { this.binding = false; }
    },
    async removeORAccount() {
      if (!(await this.confirmDialog({ title: tt('解除 OpenReview 绑定？'),
            message: tt('会删除本机保存的 OpenReview 凭据（不影响官网账号）。之后自动同步与查询会停止，可随时重新绑定。'),
            okText: tt('解除绑定'), danger: true }))) return;
      try { await this.api('/api/accounts/openreview', { method: 'DELETE' }); } catch { return; }
      this.orAccount = null; this.syncResult = null;
      this.$toast(tt('已解除绑定：本机数据库和备份里的凭据都已删除'));
      this.loadChanges({ quiet: true });
    },
    async syncMySubmissions() {
      this.syncing = true; this.syncResult = null;
      try {
        const r = await this.api('/api/sync/openreview', { method: 'POST' });
        this.syncResult = r;
        if (r.ok) {
          const d = (r.discovered || []).filter(x => !x._dismissed).length, u = (r.updated || []).length;
          this.$toast(d ? tt('同步完成：{0} 篇状态有变化，新发现 {1} 篇可一键建档', [u, d])
                        : tt('同步完成：{0} 篇状态有变化，其余无变化 ✓', [u]));
          this.refreshAll(); this.loadPollLogs(); this.loadChanges({ quiet: true });
        } else {
          this.$toast(r.error || tt('同步失败'), r.busy ? 'warn' : 'bad');
          if (r.need_rebind) this.loadORAccount();
        }
      } catch {} finally { this.syncing = false; }
    },
    async importDiscovered(d, link = false) {
      if (d._importing) return;          // 防双击重复建档
      d._importing = true;
      const body = { ...d };
      delete body._importing;
      if (link && d._related_ids?.length) body.link_related_ids = d._related_ids;
      try {
        const r = await this.api('/api/sync/openreview/import', this.jsonOpts('POST', body));
        if (r.existing && r.bound) {
          this.$toast(r.hint);
          this.openDetail(r.id);
        } else if (r.existing) {
          this.$toast(r.hint || tt('该会议下已有这篇，未重复建档'), 'warn',
            r.in_trash ? { action: { label: '去回收站', fn: () => this.goList('trash') } } : {});
        } else {
          this.$toast(r.linked?.length ? tt('已建档并关联到 {0}（转投，可在详情里改关系）✓', [r.linked.map(x => '#' + x).join(' ')])
                                       : tt('已建档 ✓'));
          this.openDetail(r.id);
        }
        // 从「新发现」列表移除
        if (this.syncResult) this.syncResult.discovered = (this.syncResult.discovered || [])
          .filter(x => x.forum !== d.forum);
        this.refreshAll();
        this.loadChanges({ quiet: true });
      } catch {} finally { d._importing = false; }
    },
    // 渠道查询页的同步结果里也能「忽略」（与总览待建档同一份记录）
    async dismissDiscovered(d) {
      try { await this.api('/api/pending-imports/dismiss', this.jsonOpts('POST', { forum: d.forum, dismissed: true })); }
      catch { return; }
      d._dismissed = true;
      this.loadChanges({ quiet: true });
      this.$toast(tt('已忽略：{0}（之后同步也不再提示）', [this.short(d.title, 30)]), 'ok', { undo: true, action: { label: '撤销', fn: async () => {
        try { await this.api('/api/pending-imports/dismiss', this.jsonOpts('POST', { forum: d.forum, dismissed: false })); } catch { return; }
        d._dismissed = false;
        this.loadChanges({ quiet: true });
      } } });
    },

    // ---------- 变化区（自上次查看以来）+ 桌面通知 ----------
    // quiet：用户自己刚做的操作（查询、同步、导入）带来的变化，只更新角标，不再重复弹提示
    async loadChanges(opts = {}) {
      let r;
      try { r = await this.api('/api/changes', { silent: true }); } catch { return; }
      const prev = this._notifiedUntil;
      const changes = r.changes || [], pending = r.pending || [];
      if (prev !== undefined && !opts.quiet) {   // 首次加载只显示角标，不弹通知
        const fresh = changes.filter(c => (c.recorded_at || '') > prev);
        const freshPend = pending.filter(p => p.new && (p.first_seen_at || '') > prev);
        if (fresh.length || freshPend.length) this.notifyChanges(fresh, freshPend);
      }
      this._notifiedUntil = [...changes.map(c => c.recorded_at || ''),
                             ...pending.filter(p => p.new).map(p => p.first_seen_at || '')]
        .reduce((a, b) => (b > a ? b : a), prev || '');
      this.changes = r;
    },
    changeLine(c) {
      const st = c.prev_stage && c.prev_stage !== c.stage
        ? `${this.stageLabel(c.prev_stage)} → ${this.stageLabel(c.stage)}`
        : `${this.stageLabel(c.stage)}（${this.short(this.rawTail(c.raw_status), 30) || tt('有更新')}）`;
      return `${this.short(c.title, 26)}：${st}`;
    },
    notifyChanges(fresh, freshPend) {
      const nC = fresh.length, nP = freshPend.length;
      const title = nC ? tt('投稿状态有 {0} 条新变化', [nC]) : tt('账号同步发现 {0} 篇新投稿待建档', [nP]);
      const lines = nC ? fresh.slice(0, 3).map(c => this.changeLine(c))
                       : freshPend.slice(0, 3).map(p => this.short((p.item && p.item.title) || '', 40));
      if (nC && nP) lines.push(tt('另有 {0} 篇新发现的投稿待建档', [nP]));
      const away = document.visibilityState !== 'visible' || (document.hasFocus && !document.hasFocus());
      const perm = typeof Notification === 'undefined' ? 'unsupported' : Notification.permission;
      this.notifyPerm = perm;                            // 以浏览器当前权限为准（用户可能刚在设置里改过）
      const inApp = () => this.$toast(
        nC ? `${title}：${lines[0]}${nC > 1 ? tt(' 等') : ''}` : tt('{0}，可在总览一键建档', [title]), 'ok',
        { action: { label: '查看', fn: () => this.goInbox() } });
      if (perm === 'granted' && away) {
        try {
          // renotify：同一个 tag 的新一批变化也要重新弹出（否则只是悄悄替换掉旧通知）
          const nt = new Notification(title, { body: lines.join('\n'), tag: 'paper-tracker', renotify: true });
          nt.onclick = () => { try { window.focus(); } catch {} this.goInbox(); nt.close(); };
          nt.onerror = inApp;
          return;
        } catch {}
      }
      inApp();
    },
    async enableNotify() {
      if (typeof Notification === 'undefined') { this.$toast(tt('这个浏览器不支持桌面通知'), 'warn'); return; }
      try { this.notifyPerm = await Notification.requestPermission(); } catch { this.notifyPerm = Notification.permission; }
      if (this.notifyPerm === 'granted') {
        this.$toast(tt('桌面通知已开启：页面开着时（放在后台也行），有新变化会弹系统通知 ✓'));
        try { new Notification(tt('桌面通知已开启'), { body: tt('投稿状态有新变化时会这样提醒你'), tag: 'paper-tracker-test' }); } catch {}
      } else if (this.notifyPerm === 'denied') {
        this.$toast(tt('浏览器没有允许通知：可点地址栏左侧的网站设置，把「通知」改为允许'), 'warn');
      }
    },
    async markAllSeen() {
      let r;
      try { r = await this.api('/api/changes/seen', this.jsonOpts('POST', { until: this.changes.now || '' })); } catch { return; }
      const prev = (r && r.previous) || '';
      await this.loadChanges({ quiet: true });
      this.$toast(tt('已全部标为已读'), 'ok', { undo: true, action: { label: '撤销', fn: async () => {
        try { await this.api('/api/changes/seen', this.jsonOpts('POST', { until: prev, reset: true })); } catch { return; }
        await this.loadChanges();
      } } });
    },
    async importPending(p, link = false) { await this.importDiscovered(p.item, link); },
    async dismissPending(p) {
      const forum = p.forum, title = p.item.title;
      try { await this.api('/api/pending-imports/dismiss', this.jsonOpts('POST', { forum, dismissed: true })); } catch { return; }
      await this.loadChanges({ quiet: true });
      this.$toast(tt('已忽略：{0}（之后同步也不再提示）', [this.short(title, 30)]), 'ok', { undo: true, action: { label: '撤销', fn: async () => {
        try { await this.api('/api/pending-imports/dismiss', this.jsonOpts('POST', { forum, dismissed: false })); } catch { return; }
        await this.loadChanges({ quiet: true });
      } } });
    },
    async dismissAllPending() {
      let r;
      try { r = await this.api('/api/pending-imports/dismiss-all', { method: 'POST' }); } catch { return; }
      const forums = (r && r.forums) || [];
      await this.loadChanges({ quiet: true });
      this.$toast(tt('已忽略 {0} 篇待建档（之后同步也不再提示）', [forums.length]), 'ok', { undo: true, action: { label: '撤销', fn: async () => {
        try { await this.api('/api/pending-imports/undismiss-all', this.jsonOpts('POST', { forums })); } catch { return; }
        await this.loadChanges({ quiet: true });
      } } });
    },
    async undismissAll() {
      try { await this.api('/api/pending-imports/undismiss-all', { method: 'POST' }); } catch { return; }
      await this.loadChanges({ quiet: true });
    },

    // ---------- 改投 / 扩写（被拒后一键另投，自动关联原稿） ----------
    async openResubmit() {
      const d = this.detail;
      if (!d) return;
      if (this.editMode && !(await this.leaveEditOk())) return;
      this.editMode = false;
      this.updateEvent.open = false; this.pasteForm.open = false; this.relForm.open = false;
      this.resubForm = { ...emptyResub(), open: true,
                         relation: ['accepted', 'production'].includes(d.current_stage) ? '扩写' : '转投',   // 数据值，保持中文
                         copy_pdf: !!(d.documents && d.documents.length) };
      const f = this.resubForm;
      f.venue.type = d.venue_type || 'journal';
      if (!d.venue_base_name) return;
      try {
        const r = await this.api(`/api/rankings/similar?name=${encodeURIComponent(d.venue_base_name)}`
                                 + `&type=${encodeURIComponent(d.venue_type || '')}`, { silent: true });
        if (this.resubForm === f) { f.similar = r.items || []; f.similarBase = r.base || null; }
      } catch {}
    },
    pickSimilar(s) {
      const f = this.resubForm;
      f.venue.name = s.abbr || s.name;
      f.venue.acronym = s.abbr || '';
      if (s.type) f.venue.type = s.type;
      const conf = this.confOf(s.abbr || s.name);
      if (conf && conf.template) f.venue.tracker = 'openreview';
      else if (f.venue.tracker === 'openreview') f.venue.tracker = 'manual';
      f.suggestOpen = false;
    },
    onResubVenueInput() {
      const f = this.resubForm;
      f.venue.acronym = '';
      const q = this.stripYear(f.venue.name || '');
      clearTimeout(this._resubDeb);
      if (!q || q.length < 2) { f.suggests = []; f.suggestOpen = false; return; }
      this._resubDeb = setTimeout(async () => {
        try {
          const r = await this.api('/api/rankings/suggest?q=' + encodeURIComponent(q), { silent: true });
          if (this.resubForm !== f) return;
          f.suggests = (r.items || []).filter(s => this.stripYear(s.name) !== q);
          f.suggestOpen = f.focused && f.suggests.length > 0;
        } catch {}
      }, 260);
    },
    pickResubSuggest(s) {
      const f = this.resubForm;
      f.venue.name = s.name;
      f.venue.acronym = (s.ccf?.abbr && this.confOf(s.ccf.abbr)) ? s.ccf.abbr : '';
      if (s.ccf?.type) f.venue.type = s.ccf.type;
      const conf = this.confOf(f.venue.acronym || s.name);
      if (conf && conf.template) f.venue.tracker = 'openreview';
      f.suggestOpen = false;
    },
    async submitResubmit() {
      const f = this.resubForm, d = this.detail;
      if (!d || f.saving) return;
      const name = f.venue.name.trim();
      if (!name) { this.$toast(tt('请填写要转投的会议/期刊（可点上面的候选）'), 'warn'); return; }
      const showYear = f.venue.type === 'conference' || f.venue.tracker === 'openreview';
      const year = showYear ? String(f.venue.year || '').trim() : '';
      if (year && !/^\d{4}$/.test(year)) { this.$toast(tt('年份应为四位数字，如 2027'), 'warn'); return; }
      const conf = this.confOf(f.venue.acronym || name);
      const yearless = !!(conf && conf.template && !conf.template.includes('{year}'));
      if (f.venue.tracker === 'openreview' && !year && !yearless) {
        this.$toast(tt('OpenReview 渠道需要年份（用来组装 venue id）'), 'warn'); return;
      }
      if (f.submitted_at && f.submitted_at > localDate()) { this.$toast(tt('投稿日期不能晚于今天'), 'warn'); return; }
      f.saving = true;
      let r;
      try {
        r = await this.api(`/api/submissions/${d.id}/resubmit`, this.jsonOpts('POST', {
          venue: { name, type: f.venue.type, year, tracker: f.venue.tracker, acronym: f.venue.acronym || '',
                   journal_acronym: f.venue.tracker === 'elsevier' ? f.venue.journal_acronym.trim() : '' },
          relation: f.relation, title: f.title.trim(), submitted_at: f.submitted_at,
          manuscript_number: f.manuscript_number.trim(), copy_pdf: !!f.copy_pdf }));
      } catch { f.saving = false; return; }
      const label = `${name}${year ? ' ' + year : ''}`, rel = f.relation;
      this.resubForm = emptyResub();
      this.$toast(tt('已建立新投稿 {0}（{1}），并与原稿 #{2} 关联 ✓', [label, tt(rel), d.id]));
      this.refreshAll();
      await this.openDetail(r.id);
    },

    // ---------- 备份 ----------
    async loadBackups() {
      try { this.backups = await this.api('/api/backups', { silent: true }); } catch {}
    },
    async backupNow() {
      if (this.backingUp) return;
      this.backingUp = true;
      try {
        const r = await this.api('/api/backup', { method: 'POST' });
        this.$toast(tt('已备份：{0} ✓', [r.file]));
        await this.loadBackups();
      } catch {} finally { this.backingUp = false; }
    },
    async copyText(t) {
      try { await navigator.clipboard.writeText(t); this.$toast(tt('已复制')); }
      catch { this.$toast(tt('复制失败：请手动选中文字复制'), 'warn'); }
    },

    // ---------- 导出（全部 / 当前列表） ----------
    exportCsv(current = false) {
      const p = new URLSearchParams();
      if (current) {
        const f = this.listFilter;
        if (f.category) p.set('category', f.category);
        if (f.stage) p.set('stage', f.stage);
        if (f.q.trim()) p.set('q', f.q.trim());
        if (f.sort) p.set('sort', f.sort);
      }
      if (i18nState.lang === 'en') p.set('lang', 'en');
      const a = document.createElement('a');
      a.href = '/api/export.csv' + (p.toString() ? '?' + p : '');
      a.download = '';
      document.body.appendChild(a); a.click(); a.remove();
    },

    // ---------- 新建向导（先丢 PDF → 缺啥补啥） ----------
    startWizard() {
      this.wizard = newWizard();
      this.go('new');
    },
    skipPdf() {
      // 「没有 PDF，直接手填」：清掉之前解析 PDF 自动填入的所有内容，从空表单开始
      this.wizard = { ...newWizard(), step: 2 };
    },
    pickTracker(t) {
      const w = this.wizard;
      w.tracker = t;
      w.trackerHint = '';
      if (t === 'openreview') w.venue.type = 'conference';
      else if (t === 'elsevier') w.venue.type = 'journal';
      if (w.venue.name.trim()) this.lookupWizardRank(w.venue.type);
    },
    setWizardType(t) {
      this.wizard.venue.type = t;
      if (this.wizard.venue.name.trim()) this.lookupWizardRank(t);
    },
    confOf(name) {
      const key = this.stripYear(name || '').toUpperCase();
      return this.commonVenues.find(c => c.abbr.toUpperCase() === key) || null;
    },
    confOfVenue() {
      const w = this.wizard;
      return this.confOf(w.venue.name) || this.confOf(w.venue.acronym);
    },
    autoVenueId() {
      const w = this.wizard;
      if (w.tracker !== 'openreview' || w.venueIdTouched) return;
      const conf = this.confOfVenue();
      const year = String(w.venue.year || (w.venue.name.match(/20\d{2}/) || [])[0] || '').trim();
      if (conf && conf.template && !conf.template.includes('{year}')) {   // TMLR 等不带年份
        w.venue.venue_id = conf.template;
        return;
      }
      w.venue.venue_id = conf && conf.template && /^\d{4}$/.test(year)
        ? conf.template.replace('{year}', year) : '';
    },

    onDrop(e) {
      this.wizard.dragging = false;
      const f = e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) this.uploadPdf(f);
    },
    onPdfPick(e) {
      const f = e.target.files && e.target.files[0];
      if (f) this.uploadPdf(f);
      e.target.value = '';
    },
    async uploadPdf(file) {
      if (!file.name.toLowerCase().endsWith('.pdf')) { this.$toast(tt('请选择 PDF 文件'), 'warn'); return; }
      this.wizard.parsing = true;
      try {
        const fd = new FormData();
        fd.append('file', file);
        const d = await this.api('/api/parse-pdf', { method: 'POST', body: fd, silent: true });
        // 换一个 PDF：清掉上一份草稿自动填入的内容
        if (this.wizard.draft) this.wizard = { ...newWizard(), step: 1 };
        this.wizard.draft = d;
        this._applyDraft(d);
        this.$toast(tt('PDF 解析完成，请核对下一步补录缺失字段'));
      } catch (e) {
        this.$toast(tt('解析失败：{0}', [e.message]), 'bad');
      } finally { this.wizard.parsing = false; }
    },

    async _applyDraft(d) {
      // 草稿填充（仅在空时填，不覆盖用户改动）
      const w = this.wizard, f = w.form;
      if (!f.title && d.title) f.title = d.title;
      if (!f.authorsText && d.authors?.length) f.authorsText = d.authors.join(', ');
      if (!f.abstract && d.abstract) f.abstract = d.abstract;
      if (!f.keywordsText && d.keywords?.length) f.keywordsText = d.keywords.join(', ');
      if (!f.manuscript_number && d.manuscript_number) f.manuscript_number = d.manuscript_number;
      if (!f.corresponding_pinyin && d.corresponding_pinyin) f.corresponding_pinyin = d.corresponding_pinyin;
      if ((!f.submitted_at || f.submitted_at === localDate()) && d.submitted_hint) f.submitted_at = d.submitted_hint;
      if (d.arxiv && !f.notes.includes('arXiv')) f.notes = (f.notes ? f.notes + ' ' : '') + `arXiv:${d.arxiv}`;
      if (d.doi && !f.notes.includes('DOI')) f.notes = (f.notes ? f.notes + ' ' : '') + `DOI:${d.doi}`;
      // 渠道自动预选（会议水印优先于版式判断）
      w.autoTracker = true;
      w.trackerHint = '';
      if (d.conf_name) {                                 // 会议水印 → 会议
        w.venue.type = 'conference';
        if (!w.venue.name) w.venue.name = d.conf_name;
        if (!w.venue.year && d.conf_year) w.venue.year = String(d.conf_year);
        // OpenReview 承办的（NeurIPS / ICLR / CVPR 等有模板的）走 openreview，其余手动
        const conf = this.confOf(d.conf_name);
        w.tracker = conf && conf.template ? 'openreview' : 'manual';
      } else if (d.layout === 'editorial_manager') {    // EM 封面：爱思唯尔 / Springer 等都在用
        w.tracker = 'elsevier'; w.venue.type = 'journal';
        if (!w.venue.name && d.journal_name) w.venue.name = d.journal_name;
        if (!w.venue.journal_acronym && d.manuscript_number) {
          const prefix = d.manuscript_number.split('-')[0];
          if (prefix && prefix.length <= 8) w.venue.journal_acronym = prefix;
        }
      } else {
        w.autoTracker = false;
      }
      // 识别出名称 → 自动查分级；EM 封面再按出版社确认是不是爱思唯尔期刊
      if (w.venue.name) {
        const card = await this.lookupWizardRank(w.venue.type);
        const pub = card?.ccf?.publisher || '';
        if (d.layout === 'editorial_manager' && !d.conf_name && w.tracker === 'elsevier') {
          if (pub && !/elsevier/i.test(pub)) {
            w.tracker = 'manual';
            w.trackerHint = tt('（{0} 期刊，爱思唯尔查稿通道不适用，已改为手动）', [pub]);
          } else if (!pub) {
            w.trackerHint = tt('（Editorial Manager 封面，未能确认出版社，默认爱思唯尔，可改）');
          }
        }
      }
    },

    // ---------- 会议/期刊联想补全（简称 → 全称） ----------
    onVenueInput(ev) {
      const w = this.wizard;
      if (ev && ev.type === 'input') w.venue.acronym = '';   // 手动改名：之前选联想带入的简称作废
      const q = this.stripYear(w.venue.name || '');
      clearTimeout(w._venDeb);
      if (!q || q.length < 2) { w.suggests = []; w.suggestOpen = false; return; }
      w._venDeb = setTimeout(async () => {
        try {
          const r = await this.api('/api/rankings/suggest?q=' + encodeURIComponent(q), { silent: true });
          w.suggests = (r.items || []).filter(s => this.stripYear(s.name) !== q);
          w.suggestOpen = !!w.venueFocused && w.suggests.length > 0;   // 输入框已失焦就不再弹出
        } catch {}
        this.lookupWizardRank(w.venue.type);             // 打字停下就刷新分级预览
      }, 260);
    },
    onVenueBlur() {
      const w = this.wizard;
      w.venueFocused = false;
      w.suggestOpen = false;
      if (this.stripYear(w.venue.name) !== w.rankProbeName) this.lookupWizardRank(w.venue.type);
    },
    async pickSuggest(s) {
      const w = this.wizard;
      // 选中全称时保留简称做 OpenReview 模板匹配（CVPR 的全称匹配不到模板）
      w.venue.acronym = (s.ccf?.abbr && this.confOf(s.ccf.abbr)) ? s.ccf.abbr : '';
      w.venue.name = s.name;
      w.suggestOpen = false;
      if (s.ccf?.type) w.venue.type = s.ccf.type;
      await this.lookupWizardRank(w.venue.type);
    },

    async createSub() {
      const w = this.wizard, f = w.form;
      if (!f.title.trim()) { this.$toast(tt('标题不能为空'), 'warn'); return; }
      if (!w.venue.name.trim()) { this.$toast(tt('请填写会议/期刊名称（可联想补全）'), 'warn'); return; }
      const showYear = w.venue.type === 'conference' || w.tracker === 'openreview';
      const year = showYear ? String(w.venue.year || '').trim() : '';
      if (year && !/^\d{4}$/.test(year)) { this.$toast(tt('年份应为四位数字，如 2027'), 'warn'); return; }
      if (f.submitted_at && f.submitted_at > localDate()) { this.$toast(tt('投稿日期不能晚于今天'), 'warn'); return; }
      const conf = this.confOfVenue();
      const yearless = !!(conf && conf.template && !conf.template.includes('{year}'));
      if (w.tracker === 'openreview' && !w.venue.venue_id.trim() && !year && !yearless) {
        this.$toast(tt('OpenReview 渠道需要年份（用来组装 venue id），或直接填写 OpenReview venue id'), 'warn'); return;
      }
      if (w.creating) return;
      // 只提交当前可见的字段：切换渠道/阶段后隐藏的旧值不再带上
      const venue = { ...w.venue, tracker: w.tracker, year,
                      venue_id: w.tracker === 'openreview' ? w.venue.venue_id.trim() : '',
                      journal_acronym: w.tracker === 'elsevier' ? w.venue.journal_acronym.trim() : '',
                      ddl: w.venue.type === 'conference' ? w.venue.ddl : '' };
      const split = s => s.split(/[,，;；]/).map(x => x.trim()).filter(Boolean);
      const body = {
        title: f.title.trim(),
        authors: split(f.authorsText),
        corresponding_pinyin: f.corresponding_pinyin.trim(),
        abstract: f.abstract.trim(),
        keywords: split(f.keywordsText),
        manuscript_number: f.manuscript_number.trim(),
        submitted_at: f.submitted_at,
        revise_due: f.initial_stage === 'revise' ? f.revise_due : '',
        notes: f.notes.trim(),
        initial_stage: f.initial_stage,
        openreview_forum: w.tracker === 'openreview' ? f.openreview_forum.trim() : '',
        upload_id: w.draft?.upload_id || '',
        file_name: w.draft?.file_name || '',
        venue,
      };
      w.creating = true;
      let r;
      try {
        r = await this.api('/api/submissions', this.jsonOpts('POST', body));
      } catch { w.creating = false; return; }
      w.creating = false;
      this.$toast(tt('建档成功 ✓ 时间线已留痕'));
      // 切到新稿件所在的分类，保证建完就能在列表里看到它
      const cat = Object.keys(this.categories).find(k => this.categories[k].includes(f.initial_stage));
      this.wizard = newWizard();            // 建完清空向导：后退回到这一页也不会重复建档
      this.listFilter = { stage: '', q: '', category: cat ?? '', sort: this.listFilter.sort || 'updated' };
      this.page = 'list';
      this.setHash('push');
      this.openDetail(r.id);
    },
  },
});
app.config.globalProperties.$t = tt;
app.mount('#app');
