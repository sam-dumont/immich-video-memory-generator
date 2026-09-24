import type {ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import useBaseUrl from '@docusaurus/useBaseUrl';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';
import styles from './index.module.css';

function HeroSection() {
  return (
    <header className={styles.hero}>
      <div className="container">
        <div className={styles.heroInner}>
          <div className={styles.heroText}>
            <Heading as="h1" className={styles.heroTitle}>
              Your Immich library,<br />turned into video memories
            </Heading>
            <p className={styles.heroSubtitle}>
              Pick a month, a year, a trip or a person. It finds the stories in that
              period, keeps the pictures and videos that tell them in the order they were
              taken, and renders the film with titles, maps and music.
            </p>
            <p className={styles.heroSubtitle}>
              <strong>Runs on your NAS. No GPU, no AI service.</strong> A GPU or a model
              makes it faster or polishes the draft, if you have one.
            </p>
            <div className={styles.heroCtas}>
              <Link className={styles.ctaPrimary} to="/docs/get-started/quick-start">
                Quick start
              </Link>
              <Link className={styles.ctaSecondary} to="/docs/">
                What it does
              </Link>
            </div>
          </div>
          <div className={styles.heroVisual}>
            <video
              className={styles.heroScreenshot}
              poster={useBaseUrl('/img/trip-map-flyover.jpg')}
              controls
              muted
              playsInline
              preload="metadata">
              <source src={useBaseUrl('/demo/trip-preview.mp4')} type="video/mp4" />
            </video>
            <p className={styles.heroCredit}>
              A finished trip film. CC0 stock pictures from StockSnap and Wikimedia Commons,{' '}
              <a href="https://github.com/sam-dumont/immich-video-memory-generator/blob/main/tests/e2e/fixtures/library/CREDITS.md">
                credited with their authors
              </a>
              .
            </p>
          </div>
        </div>
      </div>
    </header>
  );
}

function QuickstartSection() {
  return (
    <section className={styles.quickstart}>
      <div className="container">
        <Heading as="h2" className={styles.sectionTitle}>
          What it takes to stand up
        </Heading>
        <div className={styles.quickstartGrid}>
          <div className={styles.quickstartCode}>
            <div className={styles.codeBlock}>
              <div className={styles.codeHeader}>
                <span className={styles.codeDot} style={{background: '#ff5f57'}} />
                <span className={styles.codeDot} style={{background: '#febc2e'}} />
                <span className={styles.codeDot} style={{background: '#28c840'}} />
                <span className={styles.codeLabel}>terminal</span>
              </div>
              <pre className={styles.codeContent}>
{`mkdir -p immich-memories/output && cd immich-memories
curl -O https://raw.githubusercontent.com/sam-dumont/\\
immich-video-memory-generator/main/docker-compose.yml
export IMMICH_URL=http://your-immich-server:2283
export IMMICH_API_KEY=your-key-here
docker compose up -d

# the small CPU classifiers, once
docker compose exec immich-memories \\
  immich-memories models fetch

# then open http://localhost:8080`}
              </pre>
            </div>
            <p className={styles.quickstartAlt}>
              That is the whole stack: one container next to Immich, on the NAS you already
              have. No model server, no API key. The{' '}
              <Link to="/docs/get-started/quick-start">Quick start</Link> walks it step by step,
              and <Link to="/docs/better/overview">Make it better</Link> covers the optional
              GPU and model add-ons.
            </p>
          </div>
          <div className={styles.quickstartSteps}>
            <p className={styles.quickstartAlt} style={{marginTop: 0}}>
              Once it is up, every memory is the same four moves:
            </p>
            <div className={styles.step}>
              <span className={styles.stepNumber}>1</span>
              <div>
                <strong>Brief</strong>
                <p>Pick a memory type, its period or person, one duration</p>
              </div>
            </div>
            <div className={styles.step}>
              <span className={styles.stepNumber}>2</span>
              <div>
                <strong>Cut</strong>
                <p>It finds the stories in the period and gives each its share</p>
              </div>
            </div>
            <div className={styles.step}>
              <span className={styles.stepNumber}>3</span>
              <div>
                <strong>Storyboard</strong>
                <p>See every shot before it renders; untick what you disagree with</p>
              </div>
            </div>
            <div className={styles.step}>
              <span className={styles.stepNumber}>4</span>
              <div>
                <strong>Export</strong>
                <p>Render with map animations, titles, music</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

type ShowcaseItem = {
  title: string;
  description: string;
  image: string;
  alt: string;
};

const showcaseItems: ShowcaseItem[] = [
  {
    title: '10 memory types',
    description: 'Year in Review, Season, Person Spotlight, Multi-Person, Monthly Highlights, On This Day, Album, Trip, Holiday, and Surprise Me (a day your library says something happened on). Pick a type and it handles the rest, or take Custom date range and set the dates yourself.',
    image: '/img/screenshots/memory-brief.png',
    alt: 'The brief: memory type, its parameters and the duration line',
  },
  {
    title: 'An editor, not a filter',
    description: 'It groups the period into moments and stories, gives a trip or a birthday more room than an ordinary Tuesday, keeps your favourites, lets a video carry its moment, and plays everything in the order it happened. On the NAS, from your library alone. The rules are all written down.',
    image: '/img/screenshots/memory-story.png',
    alt: 'The story the cut produced, with a reason for every picture',
  },
  {
    title: 'Cinematic title screens',
    description: 'Animated gradients, particle systems, satellite trip maps. The title kernels use a Metal, CUDA or Vulkan GPU when there is one and the CPU otherwise. The log says which one ran.',
    image: '/img/screenshots/memory-options.png',
    alt: 'Generation options with title and music settings',
  },
  {
    title: 'Music that ducks',
    description: 'Your own file or one of 28 bundled tracks, picked by the mood of the cut. A sidechain compressor ducks the music under the clips\' own sound. With a music server, ACE-Step or MusicGen writes an original track instead.',
    image: '/img/screenshots/memory-options.png',
    alt: 'Music preview and generation options',
  },
];

function ShowcaseSection() {
  return (
    <section className={styles.showcase}>
      <div className="container">
        <Heading as="h2" className={styles.sectionTitle}>
          What it actually does
        </Heading>
        <div className={styles.showcaseGrid}>
          {showcaseItems.map((item, idx) => (
            <div key={idx} className={styles.showcaseCard}>
              <img
                src={useBaseUrl(item.image)}
                alt={item.alt}
                className={styles.showcaseImage}
                loading="lazy"
              />
              <div className={styles.showcaseContent}>
                <Heading as="h3" className={styles.showcaseTitle}>{item.title}</Heading>
                <p>{item.description}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function ValuesSection() {
  return (
    <section className={styles.values}>
      <div className="container">
        <div className={styles.valuesGrid}>
          <div className={styles.value}>
            <div className={styles.valueIcon}>
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
              </svg>
            </div>
            <strong>Your data stays home</strong>
            <p>A default run talks to your Immich server and nothing else. No telemetry, no account. Map tiles and place names are two switches, off until you turn them on, and a model gets pictures only if you configure one.</p>
          </div>
          <div className={styles.value}>
            <div className={styles.valueIcon}>
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
              </svg>
            </div>
            <strong>Read-only by default</strong>
            <p>Your originals are never modified. Upload-back is opt-in, and the only thing it ever trashes is its own superseded render of the same memory.</p>
          </div>
          <div className={styles.value}>
            <div className={styles.valueIcon}>
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
              </svg>
            </div>
            <strong>Cinematic title screens</strong>
            <p>Satellite map fly-overs, particle systems, 5 visual styles. Not "clip 1, clip 2, clip 3": actual production polish.</p>
          </div>
          <div className={styles.value}>
            <div className={styles.valueIcon}>
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
              </svg>
            </div>
            <strong>One decision a day</strong>
            <p>Schedule <code>immich-memories auto run</code> daily. It picks one eligible memory or retries one pending delivery, with variety rules that stop repeat spam.</p>
          </div>
        </div>
      </div>
    </section>
  );
}

function CtaSection() {
  return (
    <section className={styles.finalCta}>
      <div className="container">
        <Heading as="h2" className={styles.ctaTitle}>
          Your videos deserve better than a camera roll
        </Heading>
        <p className={styles.ctaDescription}>
          One container on the NAS you already have. Cut your first month tonight.
        </p>
        <div className={styles.heroCtas}>
          <Link className={styles.ctaPrimary} to="/docs/get-started/quick-start">
            Quick start
          </Link>
          <Link className={styles.ctaSecondary} to="/docs/run/requirements">
            Requirements
          </Link>
        </div>
      </div>
    </section>
  );
}

export default function Home(): ReactNode {
  return (
    <Layout
      title="Home"
      description="Turn your Immich library into memory films: a month, a year, a trip, one person. Runs on your NAS with no GPU and no AI service. Titles, maps and music, self-hosted.">
      <HeroSection />
      <QuickstartSection />
      <ShowcaseSection />
      <ValuesSection />
      <CtaSection />
    </Layout>
  );
}
