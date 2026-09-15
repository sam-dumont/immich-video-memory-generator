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
              Point it at your Immich server. Pick a year, a person, or a trip.
              An editor reads the period, weighs its stories and writes down why every
              picture is in. Then it renders: animated maps, generated music, title
              screens. Self-hosted. No subscription.
            </p>
            <div className={styles.heroCtas}>
              <Link className={styles.ctaPrimary} to="/docs/deploy/self-hosting">
                Get started
              </Link>
              <Link className={styles.ctaSecondary} to="/docs/">
                See what it does
              </Link>
            </div>
          </div>
          <div className={styles.heroVisual}>
            <img
              src={useBaseUrl('/img/screenshots/memory-story.png')}
              alt="The story view: thesis, stories in weight order, and the pictures each was granted, with reasons"
              className={styles.heroScreenshot}
              loading="eager"
            />
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
{`# 1. The app itself
export IMMICH_URL=https://photos.example.com
export IMMICH_API_KEY=your-key-here
docker compose up -d

# 2. The model files it checks at every run:
#    a pinned ONNX encoder and two CPU detectors
immich-memories models fetch

# 3. Two model servers on hardware you own:
#    a vision reader (~17 GB resident at 4-bit) and a
#    caption endpoint. Point config.yaml at both, then:
immich-memories preflight`}
              </pre>
            </div>
            <p className={styles.quickstartAlt}>
              Step 3 is the real cost: the editor reads your pictures before it cuts them, and it
              refuses to guess without them. The cheapest thing anyone has run end to end is one
              32 GB Apple Silicon Mac. The{' '}
              <Link to="/docs/deploy/self-hosting">self-hosting guide</Link> walks all of it in
              order.
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
                <p>The editor reads the period and weighs its stories</p>
              </div>
            </div>
            <div className={styles.step}>
              <span className={styles.stepNumber}>3</span>
              <div>
                <strong>Story</strong>
                <p>Read what it chose and why; trim or exclude if you like</p>
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
    title: 'A curator, not a filter',
    description: 'A small vision model captions every picture once. A text model then reads the period as a story, weighs its stories in words and grants each the pictures it earns, with a written reason for every one, favourites as indicators, and strictly chronological order. The editor is the product.',
    image: '/img/screenshots/memory-story.png',
    alt: 'The story the cut produced, with a reason for every picture',
  },
  {
    title: 'Cinematic title screens',
    description: 'Animated gradients, particle systems, satellite trip maps. Two renderers: GPU kernels if they find a Metal, CUDA or Vulkan backend, PIL everywhere else. The log says which one actually ran.',
    image: '/img/screenshots/memory-options.png',
    alt: 'Generation options with title and music settings',
  },
  {
    title: 'AI music generation',
    description: 'A vision LLM detects the mood of your clips. ACE-Step or MusicGen creates an original soundtrack. A sidechain compressor ducks the music under the clip\'s own audio.',
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
            <p>No telemetry, no account. Runs on your hardware; the only outbound calls are the ones you configure (LLM, music server, notifications) plus map tiles and geocoding for trip maps. The Immich API key never leaves your network.</p>
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
          Three services, all of them yours. The self-hosting guide is one page, in order.
        </p>
        <div className={styles.heroCtas}>
          <Link className={styles.ctaPrimary} to="/docs/deploy/self-hosting">
            Get started
          </Link>
          <Link className={styles.ctaSecondary} to="/docs/deploy/self-hosting">
            Self-hosting guide
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
      description="Turn your Immich photo library into video memories. An editor reads the period and writes down why every picture is in. Animated maps, generated music, title screens. Self-hosted, no cloud API.">
      <HeroSection />
      <QuickstartSection />
      <ShowcaseSection />
      <ValuesSection />
      <CtaSection />
    </Layout>
  );
}
