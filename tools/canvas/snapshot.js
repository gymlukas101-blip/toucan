// Executed in the rendered Canvas document. Return only fields from its presentation.
(body, kind) => {
    const visible = el => el && el.getClientRects().length > 0
        && el.checkVisibility({visibilityProperty: true, opacityProperty: true});
    const all = (root, selector) => [...root.querySelectorAll(selector)].filter(visible);
    const text = el => (el?.innerText || '').replace(/\s+/g, ' ').trim();
    const first = (root, selector) => all(root, selector)[0];
    const value = (el, attr) => el?.getAttribute(attr) || '';
    const main = body.querySelector('#content, main, [role="main"]') || body;
    if (kind === 'courses') {
        const anchors = all(body, 'a[href]').filter(a => a.origin === location.origin
            && /^\/courses\/[0-9]+\/?$/.test(a.pathname));
        const cards = [];
        for (const link of anchors) {
            const card = link.closest('.ic-DashboardCard, [data-testid="dashboard-card"]');
            const row = link.closest('tr');
            const tray = link.closest('#nav-tray-portal, [role="dialog"], nav[aria-label*="course" i], [role="navigation"][aria-label*="course" i]');
            if (!card && !row && !tray && !link.closest('#dashboard, #dashboard_cards, .course-list')) continue;
            const title = text(card && first(card, '.ic-DashboardCard__header-title, [data-testid="course-name"]'))
                || text(link) || value(link, 'aria-label') || link.title;
            if (!title || (!card && !row && /^(?:home|courses|all courses)$/i.test(title))) continue;
            const code = text(card && first(card, '.ic-DashboardCard__header-subtitle'))
                || text(row && first(row, '.course-list-course-code-column, [data-course-code]'));
            cards.push({title, code, href: link.href,
                alias: link.title || '', source: card ? 'card' : row ? 'table' : 'navigation'});
        }
        return {page: location.pathname, heading: document.title, elements: cards.length, cards};
    }

    const selector = 'li.context_module_item, [data-module-item-id], [data-testid="module-item"]';
    const groups = all(main, '.context_module, [data-testid="module-container"]');
    const groupOf = row => row.closest('.context_module, [data-testid="module-container"]');
    const heading = group => text(group && first(group, 'h2, .ig-header-title, [role="heading"]'));
    const items = all(main, selector).filter(el => !el.parentElement?.closest(selector));
    const rows = items.map(row => {
        const group = groupOf(row);
        const titleNode = first(row, '.item_name .title, .item_name a, .ig-title, [data-testid="module-item-title"]')
            || first(row, '.module-item-title, h3, a[href]');
        const link = titleNode?.matches('a[href]') ? titleNode : titleNode && first(titleNode, 'a[href]');
        const labelEl = first(row, '.requirement-description, .completion_requirement, [data-testid="completion-requirement"]');
        // Canvas renders the short visual label on the first line and its screen-reader
        // expansion on the next. Non-displayed templates have no innerText in this read.
        const labels = (labelEl?.innerText || '').split(/\n+/).map(s => s.replace(/\s+/g, ' ').trim()).filter(Boolean);
        const icons = all(row, '.module-item-status-icon :is(i,svg,img,[title],[aria-label]), [data-testid="completion-icon"], .icon-lock, .icon-locked, [data-icon="lock"], [aria-label="Locked" i], img[alt="Locked" i]');
        const statusIcons = icons.map(el => ({name: value(el, 'aria-label') || value(el, 'title') || value(el, 'alt'),
            type: [value(el, 'class'), value(el, 'data-icon'), value(el, 'data-testid')].join(' ').trim()}));
        const check = statusIcons.some(i => /(?:^|[\s_-])(?:check(?:mark)?|check-circle|completed)(?:$|[\s_-])/i.test(i.type)
            || /^completed?$/i.test(i.name))
            || /^[✓✔☑]$/.test(text(first(row, '.module-item-status-icon')));
        const iconLock = statusIcons.some(i => /(?:^|[\s_-])lock(?:ed)?(?:$|[\s_-])/i.test(i.type)
            || /\blocked\b/i.test(i.name)) || text(first(row, '.module-item-status-icon')) === '🔒';
        const type = first(row, '.type_icon, [data-testid="module-item-type"]');
        const header = row.matches('.context_module_sub_header') || /sub\s*header/i.test(type?.title || text(type));
        const style = titleNode && getComputedStyle(titleNode);
        const rgb = style?.color.match(/\d+(\.\d+)?/g)?.slice(0, 3).map(Number) || [];
        const grey = rgb.length === 3 && Math.max(...rgb) - Math.min(...rgb) < 12
            && Math.min(...rgb) >= 100 && Math.max(...rgb) <= 195;
        const disabled = !!titleNode?.closest('[aria-disabled="true"], .locked_title, .locked, .disabled, .greyed, .grayed, .text-muted')
            || !!group?.matches('.locked, .progression_locked, [aria-disabled="true"]')
            || !!(group && first(group, '.ig-header .icon-lock, .ig-header [aria-label="Locked" i]'))
            || Number(getComputedStyle(row).opacity) < .65 || (style && Number(style.opacity) < .65);
        return {id: row.id || value(row, 'data-module-item-id'), group: heading(group), title: text(titleNode),
            href: link?.href || '', label: labels[0] || '', labels, statusIcons, check,
            locked: !header && !!(disabled || iconLock || grey), header, kind: type?.title || text(type),
            text: row.innerText.trim()};
    });
    const sections = groups.map(g => ({title: heading(g),
        collapsed: !!first(g, '.ig-header [aria-expanded="false"], h2 [aria-expanded="false"]')
            || g.classList.contains('collapsed')}));
    const empty = !rows.length && /\b(?:no modules (?:have been|are|yet|published)|there are no modules)\b/i.test(main.innerText);
    return {page: location.pathname, heading: document.title, elements: rows.length, sections, rows, empty};
}
