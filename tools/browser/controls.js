// Playwright supplies descendants of the selected surface, including open shadow roots.
(elements, course) => {
    const visible = e => e.checkVisibility({visibilityProperty: true, opacityProperty: true})
        && e.getClientRects().length > 0;
    // Storyline can make its accessibility button transparent while showing a clickable
    // shape with the same model ID. Inactive slides can retain their old buttons.
    const artworkTargets = new Map();
    if (course) {
        for (const e of elements) {
            if (!e.classList.contains('acc-shadow-el') || !e.id.startsWith('acc-') || visible(e)) continue;
            const id = CSS.escape(e.id.slice(4));
            const shapes = [...e.getRootNode().querySelectorAll(
                `.slide-object.shown.cursor-hover[data-model-id="${id}"]`)].filter(visible);
            if (shapes.length === 1 && !shapes[0].closest('[inert],[aria-hidden=true].slide')) {
                artworkTargets.set(e, shapes[0]);
            }
        }
    }
    // Moodle's numbered activity index is coursework navigation within this vendor.
    const courseActivity = e => course && e.tagName === 'A' && location.hostname === 'wisc.prevent.zone'
        && e.origin === location.origin && !!e.closest('#course-index,.courseindex,.course-content,.activity,.block_navigation')
        && /^(?:\d+[.)]\s*)?(?:initial survey|expectations and motivations|the science behind alcohol and drugs|making informed decisions|final quiz|final survey)$/i
            .test((e.innerText || e.getAttribute('aria-label') || '').trim());
    const blockedSurface = e => {
        for (let node = e; node; node = node.getRootNode().host) {
            if (node.closest('[inert], .quiz__card:not(.quiz__card--active)')) return true;
            if (courseActivity(e) && node.closest('.courseindex-item.restrictions.dimmed')) return true;
            if (e.tagName === 'A' && !courseActivity(e) && node.closest('nav, [role=navigation], #breadcrumbs, #section-tabs, footer')) return true;
        }
        return false;
    };
    const clean = text => (text || '').trim().replace(/\s+/g, ' ');
    const candidates = elements.map((e, index) => ({e, index})).filter(({e}) =>
        !blockedSurface(e) && (visible(e) || artworkTargets.has(e) || [...e.labels || []].some(visible)));
    const active = candidates.filter(({e}) => !e.matches(':disabled, [aria-disabled=true]'));
    const controls = [];
    const groupKeys = new Map();
    for (const {e, index} of candidates) {
        const tag = e.tagName;
        const disabled = e.matches(':disabled, [aria-disabled=true]');
        const role = e.getAttribute('role') || (tag === 'A' ? 'link'
            : e.matches('input[type=radio],input[type=checkbox]') ? e.type
            : e.matches('textarea, input:not([type]),input[type=text], [contenteditable=true]') ? 'textbox'
            : tag === 'SELECT' ? 'combobox' : e.matches('input[type=range]') ? 'slider' : 'button');
        if (tag === 'A' && role !== 'button' && !/button|btn|cta/i.test(e.className || '')) {
            // Rise renders its numbered continuation as a standalone control surface.
            const nextLesson = e.closest('.lesson-nav--next');
            const sameLessonDocument = e.origin === location.origin && e.pathname === location.pathname
                && e.search === location.search && /^#\/lessons\/[^/]+$/.test(e.hash);
            const surface = nextLesson && sameLessonDocument ? active.filter(c => nextLesson.contains(c.e)) : active;
            if ((surface.length !== 1 || surface[0].e !== e) && !courseActivity(e)) continue;
        }
        const root = e.getRootNode();
        const referenced = (e.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
            .map(id => root.getElementById(id)?.textContent).filter(Boolean).join(' ');
        const name = clean(referenced || e.getAttribute('aria-label')
            || [...e.labels || []].map(l => l.innerText).join(' ').trim() || e.innerText || e.title
            || (e.matches('input[type=submit],input[type=button]') ? e.value : ''));
        if (!name && !['textbox','combobox','slider'].includes(role)) continue;
        const choice = role === 'radio' || role === 'checkbox';
        const info = {index, name, role, disabled, is_answer: choice || e.matches(
            'textarea,select,[contenteditable=true],[role=textbox],[role=combobox],input:not([type=range]):not([type=button]):not([type=submit]):not([type=image]):not([type=reset])')};
        info.toggle = e.hasAttribute('aria-pressed') || e.hasAttribute('aria-checked');
        if (choice || info.toggle) info.checked = e.checked === true || e.getAttribute('aria-checked') === 'true'
            || e.getAttribute('aria-pressed') === 'true';
        if (course) {
            const question = e.closest('.display_question,.question,.que,.form-group,[data-question]');
            const section = e.closest('fieldset,[role=radiogroup],[role=group]') || question;
            const title = question?.querySelector('.question_text,.qtext,[data-question-text],.question-header,legend')
                || section?.querySelector('legend,[data-question-text],.question-header');
            if (choice && section && !groupKeys.has(section))
                groupKeys.set(section, section.id || 'group-at-' + index);
            info.group = choice ? [question?.id, groupKeys.get(section),
                role === 'radio' || !section ? e.getAttribute('name') : ''].filter(Boolean).join('/') || 'choices' : '';
            info.question = choice ? clean(title?.innerText || section?.getAttribute('aria-label')) : '';
            info.id = e.id;
            if (artworkTargets.has(e)) info.artworkTarget = artworkTargets.get(e).dataset.modelId;
            info.newWindow = tag === 'A' && e.target === '_blank';
            info.inForm = !!e.closest('form');
            info.artwork = '';
            info.prompt = '';
            if (e.classList.contains('acc-shadow-el')) {
                const shapes = [...root.querySelectorAll('.slide-object.shown[data-acc-text]')].filter(visible);
                info.artwork = shapes.find(s => 'acc-' + s.dataset.modelId === e.id)?.dataset.accText?.trim() || '';
                if (info.artwork) info.prompt = shapes.map(s => s.dataset.accText.trim()).find(t => t.endsWith('?')) || '';
            }
        }
        controls.push(info);
    }
    const counts = new Map();
    const key = c => JSON.stringify([c.role, c.name, c.group || '']);
    for (const c of controls.filter(c => !c.disabled)) counts.set(key(c), (counts.get(key(c)) || 0) + 1);
    const positions = new Map();
    for (const c of controls.filter(c => !c.disabled && counts.get(key(c)) > 1)) {
        c.position = (positions.get(key(c)) || 0) + 1;
        c.peerCount = counts.get(key(c));
        positions.set(key(c), c.position);
    }
    return controls;
}
