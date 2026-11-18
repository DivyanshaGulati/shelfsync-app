import Icon from './Icon.jsx'
import cpvcPipe from '../assets/cpvc-pipe.webp'
import epoxyPutty from '../assets/epoxy-putty.webp'
import gypsumScrews from '../assets/gypsum-screws.webp'
import hinges from '../assets/hinges.webp'
import plugs from '../assets/plugs.webp'
import pvcElbow from '../assets/pvc-elbow.webp'
import segmentedBlade from '../assets/segmented-blade.webp'
import tape from '../assets/tape.webp'
import teflon from '../assets/teflon.webp'
import tileBlade from '../assets/tile-blade.webp'
import wire from '../assets/wire.webp'

const pictures = new Map([
  ['HW-SCR-15', gypsumScrews],
  ['HW-GIT-05', plugs],
  ['HW-GIT-06', plugs],
  ['HW-HNG-04', hinges],
  ['PL-TEF-01', teflon],
  ['PL-TAP-40', teflon],
  ['PL-CPV-34', cpvcPipe],
  ['PL-ELB-34', pvcElbow],
  ['EL-WIR-25', wire],
  ['EL-TAP-01', tape],
  ['EL-TAP-30', tape],
  ['TL-BLD-04', tileBlade],
  ['TL-MSL-01', epoxyPutty],
])

const categoryIcons = new Map([
  ['fasteners', 'layers'],
  ['plumbing', 'plumbing'],
  ['electrical', 'electrical'],
  ['tools & adhesives', 'tools'],
  ['hardware fittings', '/inventory'],
])

export default function ProductImage({ sku, name, category, thumbnail }) {
  const picture = thumbnail ?? pictures.get(sku?.trim().toUpperCase())
    ?? (/^3\.5\s*inch\b.*\bblade\b/i.test(name?.trim() ?? '') ? segmentedBlade : null)
  return picture
    ? <img className="product-picture" src={picture} alt="" width="56" height="56" loading="lazy" decoding="async" />
    : <span className="product-picture product-placeholder" aria-hidden="true"><Icon name={categoryIcons.get(category?.trim().toLowerCase()) ?? '/inventory'} /></span>
}
