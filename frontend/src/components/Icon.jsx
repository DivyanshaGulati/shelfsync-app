const paths = {
  '/': 'M3 10 12 3l9 7v11h-6v-7H9v7H3z',
  '/insights': 'm12 3 2.3 6.7L21 12l-6.7 2.3L12 21l-2.3-6.7L3 12l6.7-2.3z',
  '/inventory': 'm3 7 9-4 9 4v11l-9 4-9-4z M3 7l9 4 9-4 M12 11v11',
  '/vendors': 'M3 10h18l-2-6H5z M5 10v11h14V10 M9 21v-6h6v6',
  '/purchasing': 'M2 3h3l3 13h11l3-10H6 M9 20h1 M17 20h1',
  contact: 'M3 5h18v14H3z M3 6l9 7 9-7',
  electrical: 'm13 2-9 12h7l-1 8 10-12h-8z',
  plumbing: 'M4 3h7v8a2 2 0 0 0 2 2h8v7h-8a9 9 0 0 1-9-9z M2 3h11 M21 11v11',
  tools: 'M14 7l3 3 4-4a6 6 0 0 1-8 8l-7 7-3-3 7-7a6 6 0 0 1 8-8z',
  check: 'M21 12a9 9 0 1 1-9-9 9 9 0 0 1 9 9z M8 12l3 3 5-6',
  alert: 'M12 3 2 21h20z M12 9v5 M12 17v1',
  layers: 'm2 8 10-5 10 5-10 5z M2 12l10 5 10-5 M2 16l10 5 10-5',
  clock: 'M21 12a9 9 0 1 1-9-9 9 9 0 0 1 9 9z M12 7v5l3 2',
  calendar: 'M4 5h16v15H4z M8 3v4 M16 3v4 M4 10h16 M8 14h1 M12 14h1 M16 14h1',
  growth: 'm3 17 6-6 4 4 8-10 M15 5h6v6',
  refresh: 'M20 11a8 8 0 0 0-14-5L3 9 M3 4v5h5 M4 13a8 8 0 0 0 14 5l3-3 M21 20v-5h-5',
  list: 'M8 6h13 M8 12h13 M8 18h13 M3 6h1 M3 12h1 M3 18h1',
  savings: 'M10 6c0-2 3-3 6-3s6 1 6 3-3 3-6 3-6-1-6-3z M10 6v5 M22 6v8c0 2-3 3-6 3h-1 M3 13c0-2 3-3 6-3s6 1 6 3-3 3-6 3-6-1-6-3z M3 13v5c0 2 3 3 6 3s6-1 6-3v-5',
  truck: 'M2 4h13v13H2z M15 9h4l3 4v4h-7 M6 17v3h2v-3 M17 17v3h2v-3',
  quote: 'M5 2h9l5 5v15H5z M14 2v6h5 M8 12h8 M8 16h6',
  trash: 'M4 6h16 M9 6V3h6v3 M6 6l1 15h10l1-15 M10 10v7 M14 10v7',
  '/checkout': 'M3 4v16 M7 4v16 M12 4v16 M15 4v16 M21 4v16',
}

export default function Icon({ name }) {
  return <svg className="ui-icon" aria-hidden="true" width="24" height="24" viewBox="0 0 24 24"
    fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" strokeLinecap="round">
    <path d={paths[name]} />
  </svg>
}
