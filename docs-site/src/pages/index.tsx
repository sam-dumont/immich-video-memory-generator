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
              Get a polished video with smart cuts, animated maps, AI music,
              and title screens. No cloud. No subscription.
            </p>
            <div className={styles.heroCtas}>
              <Link className={styles.ctaPrimary} to="/docs/welcome/quick-start">
                Get started
              </Link>
              <Link className={styles.ctaSecondary} to="/docs/">
                See what it does
              </Link>
            </div>
          </div>
          <div className={styles.heroVisual}>
            <img
              src={useBaseUrl('/img/screenshots/step2-clip-review.png')}
              alt="Clip review interface showing scored video segments"
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
          Running in 2 minutes
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
{`# Create .env with your Immich credentials
echo 'IMMICH_URL=https://photos.example.com' > .env
echo 'IMMICH_API_KEY=your-key-here' >> .env

# Start it
docker compose up -d

# Open http://localhost:8080`}
              </pre>
            </div>
            <p className={styles.quickstartAlt}>
              Or without Docker: <code>uvx immich-memories ui</code>
            </p>
          </div>
          <div className={styles.quickstartSteps}>
            <div className={styles.step}>
              <span className={styles.stepNumber}>1</span>
              <div>
                <strong>Configure</strong>
                <p>Pick memory type, time period, person</p>
              </div>
            </div>
            <div className={styles.step}>
              <span className={styles.stepNumber}>2</span>
              <div>
                <strong>Review clips</strong>
                <p>AI scores and ranks your best moments</p>
              </div>
            </div>
            <div className={styles.step}>
              <span className={styles.stepNumber}>3</span>
              <div>
                <strong>Customize</strong>
                <p>Edit title, pick music, adjust settings</p>
              </div>
            </div>
            <div className={styles.step}>
              <span className={styles.stepNumber}>4</span>
              <div>
                <strong>Generate</strong>
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
    title: '6 memory types',
    description: 'Year in review, monthly highlights, person spotlight, trip memory, season, on this day. Pick a preset and it handles the rest.',
    image: '/img/screenshots/step1-preset-cards.png',
    alt: 'Memory type preset selection cards',
  },
  {
    title: 'AI-scored clip selection',
    description: 'Scene detection, face recognition, motion analysis, and optional LLM scoring pick the moments worth keeping. Duplicates are filtered automatically.',
    image: '/img/screenshots/step2-refine-moments.png',
    alt: 'Clip review grid with scored video segments',
  },
  {
    title: 'Cinematic title screens',
    description: 'Animated gradients, particle systems, globe rendering, trip maps. Three rendering backends (Taichi GPU, PIL, FFmpeg) pick the best your hardware can do.',
    image: '/img/screenshots/step3-options.png',
    alt: 'Generation options with title and music settings',
  },
  {
    title: 'AI music generation',
    description: 'A vision LLM detects the mood of your clips. ACE-Step or MusicGen creates an original soundtrack. Audio ducking lowers music during speech.',
    image: '/img/screenshots/step3-options.png',
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
            <p>Zero telemetry. Zero cloud calls. Everything runs on your hardware. The Immich API key never leaves your network.</p>
          </div>
          <div className={styles.value}>
            <div className={styles.valueIcon}>
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
              </svg>
            </div>
            <strong>Read-only by default</strong>
            <p>Your Immich library is never modified. Upload-back is opt-in. No risk of data loss, ever.</p>
          </div>
          <div className={styles.value}>
            <div className={styles.valueIcon}>
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
              </svg>
            </div>
            <strong>Cinematic title screens</strong>
            <p>Animated globe fly-overs, satellite map zoom, particle systems, 5 visual styles. Not "clip 1, clip 2, clip 3": actual production polish.</p>
          </div>
          <div className={styles.value}>
            <div className={styles.valueIcon}>
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
              </svg>
            </div>
            <strong>Runs on a schedule</strong>
            <p>Built-in cron scheduler generates memories automatically. Monthly highlights on the 1st, yearly recap in January. Set it and forget it.</p>
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
          Install in 2 minutes. Generate your first memory in 10.
        </p>
        <div className={styles.heroCtas}>
          <Link className={styles.ctaPrimary} to="/docs/welcome/quick-start">
            Get started
          </Link>
          <Link className={styles.ctaSecondary} to="/docs/deploy/installation/docker">
            Docker setup
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
      description="Turn your Immich photo library into polished video memories. Smart clips, animated maps, AI music, title screens. Self-hosted, no cloud.">
      <HeroSection />
      <QuickstartSection />
      <ShowcaseSection />
      <ValuesSection />
      <CtaSection />
    </Layout>
  );
}
