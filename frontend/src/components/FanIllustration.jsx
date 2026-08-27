import { motion } from 'framer-motion'

/** Hand-drawn ceiling fan, viewed from below.
 *
 * Line art rather than a photograph: no asset to fetch, it inherits the ink colour,
 * and it can spin. Drawing the product ourselves keeps the storefront in the same
 * hand-set language as the rest of the product.
 */
export default function FanIllustration({ spinning = false }) {
  const blade = (rot) => (
    <g key={rot} transform={`rotate(${rot} 200 200)`}>
      <path d="M200 182 C 246 172, 300 166, 344 178 C 350 180, 352 188, 346 192
               C 300 208, 244 212, 200 204 Z"
            fill="#FBF8F1" stroke="currentColor" strokeWidth="2.2" strokeLinejoin="round" />
      <path d="M214 188 C 258 182, 300 180, 336 186" fill="none"
            stroke="currentColor" strokeWidth="1.1" opacity="0.4" />
    </g>
  )

  return (
    <div className="w-full aspect-square flex items-center justify-center text-ink">
      <motion.svg viewBox="0 0 400 400" className="w-[78%] h-auto"
        animate={spinning ? { rotate: 360 } : { rotate: 0 }}
        transition={spinning
          ? { repeat: Infinity, ease: 'linear', duration: 1.4 }
          : { duration: 0.8, ease: [0.16, 1, 0.3, 1] }}>
        {[0, 120, 240].map(blade)}
        <circle cx="200" cy="200" r="34" fill="#FBF8F1" stroke="currentColor" strokeWidth="2.4" />
        <circle cx="200" cy="200" r="20" fill="none" stroke="currentColor" strokeWidth="1.2" opacity="0.55" />
        <circle cx="200" cy="200" r="5" fill="currentColor" />
      </motion.svg>
    </div>
  )
}
