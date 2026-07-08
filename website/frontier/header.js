/**
 * header.js — injects the shared site header into #site-header.
 *
 * Usage: place <div id="site-header"></div> where the header should appear,
 * then load this script.  Pages deeper than zh-CN/ root must set:
 *   window.SITE_CONFIG = { rootPath: '../../' }  (adjust depth as needed)
 * before loading this script.
 *
 * Language switcher is hidden on blog article detail pages (/blog/articles/).
 */
(function () {
    var root = (window.SITE_CONFIG && window.SITE_CONFIG.rootPath) || '';
    var path = window.location.pathname;

    function activeClass(keyword) {
        if (!keyword) return '';
        return path.indexOf(keyword) !== -1 ? ' active' : '';
    }

    /* Determine which nav item is active based on current URL. */
    var homeActive = (path.endsWith('/') || path.endsWith('index.html')) && path.indexOf('/blog') === -1 ? ' active' : '';
    var dlActive   = activeClass('download');
    var docsActive = activeClass('docs');
    var blogActive = activeClass('blog');

    /* Language switcher — hidden on blog article detail pages. */
    var isBlogDetail = path.indexOf('/blog/articles/') !== -1;

    var LANGS = [
        { code: 'zh-CN', label: '简体中文' },
        { code: 'zh-TW', label: '繁體中文' },
        { code: 'en',    label: 'English'  },
        { code: 'ja',    label: '日本語'   },
        { code: 'ko',    label: '한국어'   }
    ];

    function getCurrentLangCode() {
        var codes = ['zh-CN', 'zh-TW', 'en', 'ja', 'ko'];
        for (var i = 0; i < codes.length; i++) {
            if (path.indexOf('/' + codes[i] + '/') !== -1) return codes[i];
        }
        return 'en';
    }

    var currentCode = getCurrentLangCode();
    var currentLabel = 'EN';
    for (var i = 0; i < LANGS.length; i++) {
        if (LANGS[i].code === currentCode) { currentLabel = LANGS[i].label; break; }
    }

    function buildLangSwitcher() {
        if (isBlogDetail) return '';
        var items = '';
        for (var j = 0; j < LANGS.length; j++) {
            var l = LANGS[j];
            var cls = l.code === currentCode ? 'lang-option active' : 'lang-option';
            items += '<button class="' + cls + '" data-lang="' + l.code + '">' + l.label + '</button>';
        }
        return '<div class="lang-switcher">' +
            '<button class="lang-btn" aria-label="Switch language" aria-expanded="false">' +
            '<i class="fa-solid fa-globe"></i>' +
            '<span class="lang-label">' + currentLabel + '</span>' +
            '<i class="fa-solid fa-chevron-down lang-chevron"></i>' +
            '</button>' +
            '<div class="lang-dropdown">' + items + '</div>' +
            '</div>';
    }

    var headerHTML = `
<header>
    <div class="brand-logo">
        <a href="${root}index.html" style="display:flex;align-items:center;gap:12px;">
            <div class="logo-box">
                <img src="${root}assets/images/Clash_Logo_r40.png" alt="Clash Logo" style="width:32px;height:32px;object-fit:contain;display:block;">
            </div>
            <span class="brand-text">Clash</span>
        </a>
    </div>

    <nav class="nav-menu">
        <a href="${root}index.html" class="${homeActive.trim()}">首页</a>
        <a href="${root}download.html" class="${dlActive.trim()}">下载</a>
        <a href="${root}docs.html" class="${docsActive.trim()}">教程</a>
        <a href="${root}blog/index.html" class="${blogActive.trim()}">博客</a>
    </nav>

    <div class="nav-right">
        ${buildLangSwitcher()}
        <a href="${root}download.html" class="btn-header">
            立即下载 <i class="fa-solid fa-arrow-down" style="margin-left:6px;font-size:12px;"></i>
        </a>
        <button class="nav-toggle" id="nav-toggle" aria-label="打开菜单" aria-expanded="false">
            <span class="nav-toggle-bar"></span>
            <span class="nav-toggle-bar"></span>
            <span class="nav-toggle-bar"></span>
        </button>
    </div>
</header>
<nav class="mobile-nav" id="mobile-nav" aria-hidden="true">
    <a href="${root}index.html" class="${homeActive.trim()}">首页</a>
    <a href="${root}download.html" class="${dlActive.trim()}">下载</a>
    <a href="${root}docs.html" class="${docsActive.trim()}">教程</a>
    <a href="${root}blog/index.html" class="${blogActive.trim()}">博客</a>
    <a href="${root}download.html" class="mobile-dl-btn">
        <i class="fa-solid fa-arrow-down"></i> 立即下载
    </a>
</nav>`;

    var el = document.getElementById('site-header');
    if (el) el.innerHTML = headerHTML;

    /* Language switching interaction */
    if (!isBlogDetail) {
        document.addEventListener('click', function (e) {
            var switcher = document.querySelector('.lang-switcher');
            if (!switcher) return;

            var btn = e.target.closest('.lang-btn');
            var opt = e.target.closest('.lang-option');

            if (btn) {
                var isOpen = switcher.classList.contains('open');
                switcher.classList.toggle('open');
                btn.setAttribute('aria-expanded', String(!isOpen));
                return;
            }

            if (opt) {
                var targetLang = opt.getAttribute('data-lang');
                var currentPath = window.location.pathname;
                var codes = ['zh-CN', 'zh-TW', 'en', 'ja', 'ko'];
                var newPath = currentPath;
                for (var k = 0; k < codes.length; k++) {
                    if (currentPath.indexOf('/' + codes[k] + '/') !== -1) {
                        newPath = currentPath.replace('/' + codes[k] + '/', '/' + targetLang + '/');
                        break;
                    }
                }
                window.location.href = newPath;
                return;
            }

            /* Click outside — close dropdown */
            if (!switcher.contains(e.target)) {
                switcher.classList.remove('open');
                var langBtn = switcher.querySelector('.lang-btn');
                if (langBtn) langBtn.setAttribute('aria-expanded', 'false');
            }
        });
    }

    /* Mobile nav toggle */
    function closeMobileNav() {
        var mobileNav = document.getElementById('mobile-nav');
        var navToggle = document.getElementById('nav-toggle');
        if (!mobileNav || !navToggle) return;
        mobileNav.classList.remove('open');
        navToggle.classList.remove('open');
        navToggle.setAttribute('aria-expanded', 'false');
        mobileNav.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
    }

    document.addEventListener('click', function (e) {
        var mobileNav = document.getElementById('mobile-nav');
        var navToggle = document.getElementById('nav-toggle');
        if (!mobileNav || !navToggle) return;

        if (e.target.closest('#nav-toggle')) {
            var isOpen = mobileNav.classList.contains('open');
            mobileNav.classList.toggle('open');
            navToggle.classList.toggle('open');
            navToggle.setAttribute('aria-expanded', String(!isOpen));
            mobileNav.setAttribute('aria-hidden', String(isOpen));
            document.body.style.overflow = isOpen ? '' : 'hidden';
            return;
        }

        if (mobileNav.classList.contains('open')) {
            var isNavLink = e.target.closest('#mobile-nav') && e.target.tagName === 'A';
            var isOutside = !e.target.closest('#mobile-nav') && !e.target.closest('#nav-toggle');
            if (isNavLink || isOutside) {
                closeMobileNav();
            }
        }
    });
})();
