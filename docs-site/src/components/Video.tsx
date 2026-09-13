import useBaseUrl from '@docusaurus/useBaseUrl';
import React from 'react';

interface VideoProps extends React.VideoHTMLAttributes<HTMLVideoElement> {
  src: string;
}

export default function Video({src, poster, ...props}: VideoProps) {
  // The site is served under a base path, so a poster written as /img/... only
  // resolves once it goes through the same helper the source does.
  const posterUrl = useBaseUrl(poster ?? '');
  return (
    <video poster={poster ? posterUrl : undefined} {...props}>
      <source src={useBaseUrl(src)} type="video/mp4" />
    </video>
  );
}
