// Playwright supplies descendants of the selected surface, including open shadow roots.
(elements, course) => {
    const visible = e => e.checkVisibility({visibilityProperty: true, opacityProperty: true})
        && e.getClientRects().length > 0;
    const blockedSurface = e => {
        for (let node = e; node; node = node.getRootNode().host) {
            if (node.closest('[inert], .quiz__card:not(.quiz__card--active)')) return true;
            if (e.tagName === 'A' && node.closest('nav, [role=navigation], #breadcrumbs, #section-tabs, footer')) return true;
        }
        return false;
    };
    const clean = text => (text || '').trim().replace(/\s+/g, ' ');
    const candidates = elements.map((e, index) => ({e, index})).filter(({e}) =>
        !blockedSurface(e) && (visible(e) || [...e.labels || []].some(visible)));
    const active = candidates.filter(({e}) => !e.matches(':disabled, [aria-disabled=true]'));
    const controls = [];
    for (const {e, index} of candidates) {
        const tag = e.tagName;
        const disabled = e.matches(':disabled, [aria-disabled=true]');
        const role = e.getAttribute('role') || (tag === 'A' ? 'link'
            : e.matches('input[type=radio],input[type=checkbox]') ? e.type
            : e.matches('textarea, input:not([type]),input[type=text], [contenteditable=true]') ? 'textbox'
            : tag === 'SELECT' ? 'combobox' : e.matches('input[type=range]') ? 'slider' : 'button');
        if (tag === 'A' && role !== 'button' && !/button|btn|cta/i.test(e.className || '')
                && (active.length !== 1 || active[0].e !== e)) continue;
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
        if (choice) info.checked = e.checked === true || e.getAttribute('aria-checked') === 'true';
        if (course) {
            const section = e.closest('.display_question,fieldset,[role=radiogroup],[role=group],.question');
            const title = section?.querySelector('.question_text,legend,[data-question-text],.question-header');
            info.group = choice ? (section?.id || clean(title?.innerText) || section?.getAttribute('aria-label')
                || e.getAttribute('name') || (section ? 'question-' + [...root.querySelectorAll('fieldset,[role=group],[role=radiogroup],.question')].indexOf(section) : 'choices')) : '';
            info.question = choice ? clean(title?.innerText || section?.getAttribute('aria-label')) : '';
            info.id = e.id;
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
    return controls;
}
