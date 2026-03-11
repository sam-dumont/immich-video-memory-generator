import type {ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';

import styles from './index.module.css';

type FeatureItem = {
  title: string;
  emoji: string;
  description: ReactNode;
};

const FeatureList: FeatureItem[] = [
  {
    title: 'Smart Clip Selection',
    emoji: '🎬',
    description: (
      <>
        Scene detection, interest scoring, and duplicate filtering pick the best
        moments from your videos. No more scrolling through hours of footage.
      </>
    ),
  },
  {
    title: 'Face-Aware Everything',
    emoji: '👤',
    description: (
      <>
        Filter by person using Immich's face recognition. Smart cropping keeps
        faces centered when converting between aspect ratios.
      </>
    ),
  },
  {
    title: 'GPU Accelerated',
    emoji: '⚡',
    description: (
      <>
        NVIDIA NVENC, Apple VideoToolbox, Intel QSV, and AMD VAAPI. Hardware
        encoding is 5-10x faster than software.
      </>
    ),
  },
  {
    title: 'AI Music Generation',
    emoji: '🎵',
    description: (
      <>
        A vision LLM detects the mood, then ACE-Step or MusicGen creates an
        original soundtrack. Audio ducking lowers music during speech.
      </>
    ),
  },
  {
    title: 'Web UI + CLI',
    emoji: '🖥️',
    description: (
      <>
        A 4-step wizard for interactive use, or a full CLI for automation
        and scripting. Docker and Kubernetes ready.
      </>
    ),
  },
  {
    title: 'Immich Native',
    emoji: '📸',
    description: (
      <>
        Built for Immich from day one. Read-only API access — your library
        is never modified. Works with face recognition, albums, and more.
      </>
    ),
  },
];

function Feature({title, emoji, description}: FeatureItem) {
  return (
    <div className={clsx('col col--4')}>
      <div className="text--center padding-horiz--md" style={{marginBottom: '2rem'}}>
        <div style={{fontSize: '3rem', marginBottom: '0.5rem'}}>{emoji}</div>
        <Heading as="h3">{title}</Heading>
        <p>{description}</p>
      </div>
    </div>
  );
}

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={clsx('hero hero--primary', styles.heroBanner)}>
      <div className="container">
        <Heading as="h1" className="hero__title">
          {siteConfig.title}
        </Heading>
        <p className="hero__subtitle">{siteConfig.tagline}</p>
        <div className={styles.buttons}>
          <Link
            className="button button--secondary button--lg"
            to="/docs/quick-start">
            Get Started
          </Link>
          <Link
            className="button button--outline button--secondary button--lg"
            to="/docs/intro"
            style={{marginLeft: '1rem'}}>
            Learn More
          </Link>
        </div>
      </div>
    </header>
  );
}

export default function Home(): ReactNode {
  return (
    <Layout
      title="Home"
      description="Create beautiful video compilations from your Immich photo library">
      <HomepageHeader />
      <main>
        <section className={styles.features}>
          <div className="container">
            <div className="row">
              {FeatureList.map((props, idx) => (
                <Feature key={idx} {...props} />
              ))}
            </div>
          </div>
        </section>
      </main>
    </Layout>
  );
}
