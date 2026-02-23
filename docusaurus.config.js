import {themes as prismThemes} from 'prism-react-renderer';
import {readFileSync} from 'fs';
import {fileURLToPath} from 'url';
import path from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const rootPackageJson = JSON.parse(
  readFileSync(path.join(__dirname, 'package.json'), 'utf8')
);
const version = rootPackageJson.version;

const GITHUB_ORG_NAME = 'HiDiHo01';
const GITHUB_REPO_NAME = 'home-assistant-frank_energie';
const GITHUB_REPO_URL = `https://github.com/${GITHUB_ORG_NAME}/${GITHUB_REPO_NAME}`;
const DOCS_FOLDER = 'docs';

const config = {
  title: 'Frank Energie',
  tagline: 'Frank Energie Home Assistant Integration',
  favicon: 'img/favicon.ico',

  future: { v4: true },

  url: `https://${GITHUB_ORG_NAME}.github.io`,
  baseUrl: `/${GITHUB_REPO_NAME}/`,
  organizationName: GITHUB_ORG_NAME,
  projectName: GITHUB_REPO_NAME,

  trailingSlash: false,
  onBrokenLinks: 'throw',

  i18n: { defaultLocale: 'en', locales: ['en'] },

  presets: [
    [
      'classic',
      ({
        docs: {
          routeBasePath: '/',
          sidebarPath: './sidebars.js',
          editUrl: ({docPath}) =>
            `${GITHUB_REPO_URL}/edit/main/${DOCS_FOLDER}/${docPath}`,
        },
        blog: false,
        theme: { customCss: './src/css/custom.css' },
      }),
    ],
  ],

  themes: [
    [
      '@easyops-cn/docusaurus-search-local',
      { hashed: true, highlightSearchTermsOnTargetPage: true },
    ],
  ],

  themeConfig: {
    navbar: {
      title: 'Frank Energie',
      logo: { alt: 'Logo?', src: 'img/logo.svg' },
      items: [
        { html: `<span>v${version}</span>`, position: 'left', className: 'navbar-version-chip', href: '#' },
        { type: 'search', position: 'right' },
        { href: `${GITHUB_REPO_URL}`, label: 'GitHub', position: 'right' },
      ],
    },
    footer: {
      style: 'dark',
      copyright: `Copyright © ${new Date().getFullYear()} Frank Energie. Built with Docusaurus.`,
    },
    colorMode: { defaultMode: 'light', disableSwitch: false, respectPrefersColorScheme: true },
    prism: { theme: prismThemes.github, darkTheme: prismThemes.dracula },
  },
};

export default config;
