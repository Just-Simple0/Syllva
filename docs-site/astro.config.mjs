import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

const repository = 'https://github.com/Just-Simple0/Syllva';

export default defineConfig({
  site: 'https://just-simple0.github.io',
  base: '/Syllva',
  integrations: [
    starlight({
      title: 'Syllva Docs',
      description: 'Official documentation for the Syllva academic context system.',
      defaultLocale: 'root',
      locales: {
        root: { label: 'English', lang: 'en' },
        ko: { label: '한국어', lang: 'ko' },
      },
      social: [{ icon: 'github', label: 'GitHub', href: repository }],
      sidebar: [
        {
          label: 'User Guide',
          translations: { ko: '사용자 가이드' },
          items: [{ autogenerate: { directory: 'user-guide' } }],
        },
        {
          label: 'Operator Guide',
          translations: { ko: '운영자 가이드' },
          items: [{ autogenerate: { directory: 'operator-guide' } }],
        },
        {
          label: 'Concepts',
          translations: { ko: '개념' },
          items: [{ autogenerate: { directory: 'concepts' } }],
        },
        {
          label: 'Reference',
          translations: { ko: '레퍼런스' },
          items: [{ autogenerate: { directory: 'reference' } }],
        },
        {
          label: 'Engineering archive',
          translations: { ko: '엔지니어링 기록' },
          link: `${repository}/tree/main/docs`,
        },
      ],
    }),
  ],
});
