import ProductImage from './ProductImage.jsx'

export default function ProductIdentity({ name, sku, unit }) {
  return <div className="product-identity">
    <ProductImage sku={sku} name={name} />
    <div>
      <strong>{name}</strong>
      <small>{sku}{unit && ` · ${unit}`}</small>
    </div>
  </div>
}
