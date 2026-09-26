// Choose the secret a connection uses. The list shows secret names from the
// installation's Secret Manager; a new key can be pasted once and is stored
// there. Values never come back to the browser.
import { KeyRoundIcon, PlusIcon } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectSeparator, SelectTrigger, SelectValue } from '@/components/ui/select'
import { api, errorMessage } from '@/lib/api'
import { useSignedIn } from '@/lib/session'
import type { SecretItem } from '@/lib/types'

const NONE = '__none__'
const MANUAL = '__manual__'
const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,62}$/

type Catalog = { items: SecretItem[]; managed_prefix: string | null; can_store: boolean }

export function secretName(ref: string | null): string {
  if (!ref) return 'Anahtar yok'
  const match = /secrets\/([^/]+)\//.exec(ref)
  return match ? match[1] : ref
}

export function SecretPicker({ value, onChange, id }: { value: string | null; onChange: (ref: string | null) => void; id?: string }) {
  const { user } = useSignedIn()
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [manual, setManual] = useState(false)
  const [slug, setSlug] = useState('')
  const [secret, setSecret] = useState('')
  const [storing, setStoring] = useState(false)

  const load = useCallback(async () => {
    try {
      setCatalog(await api<Catalog>(user, 'GET', '/v1/secrets'))
      setLoadError(null)
    } catch (error) {
      setLoadError(errorMessage(error))
    }
  }, [user])

  useEffect(() => {
    void load()
  }, [load])

  const known = catalog?.items.some((item) => item.ref === value) ?? false
  const isManual = manual || (value !== null && !known && catalog !== null)
  const selectValue = value === null ? NONE : known ? value : MANUAL

  const store = async () => {
    setStoring(true)
    try {
      const result = await api<{ ref: string }>(user, 'PUT', `/v1/secrets/${slug}`, { value: secret })
      onChange(result.ref)
      setAdding(false)
      setSecret('')
      setSlug('')
      toast.success('Anahtar Secret Manager’a kaydedildi.')
      void load()
    } catch (error) {
      toast.error(`Kaydedilemedi: ${errorMessage(error)}`)
    } finally {
      setStoring(false)
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <Select
          value={selectValue}
          onValueChange={(next) => {
            if (next === MANUAL) {
              setManual(true)
              return
            }
            setManual(false)
            onChange(next === NONE ? null : next)
          }}
        >
          <SelectTrigger id={id} className="min-w-0 flex-1">
            <SelectValue placeholder="Anahtar seç" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NONE}>Anahtar yok (anahtarsız yerel uç)</SelectItem>
            {catalog && catalog.items.length > 0 && (
              <SelectGroup>
                <SelectLabel>Secret Manager</SelectLabel>
                {catalog.items.map((item) => (
                  <SelectItem key={item.ref} value={item.ref}>
                    <KeyRoundIcon className="text-muted-foreground" /> {item.id}
                  </SelectItem>
                ))}
              </SelectGroup>
            )}
            <SelectSeparator />
            <SelectItem value={MANUAL}>Referansı elle gir…</SelectItem>
          </SelectContent>
        </Select>
        {catalog?.can_store && (
          <Button type="button" variant="outline" onClick={() => setAdding(true)}>
            <PlusIcon /> Yeni anahtar
          </Button>
        )}
      </div>
      {isManual && (
        <Input
          value={value ?? ''}
          onChange={(event) => onChange(event.target.value.trim() || null)}
          placeholder="env:DEGISKEN veya projects/…/secrets/…/versions/…"
          className="font-mono text-xs"
        />
      )}
      {loadError && <p className="text-muted-foreground text-xs">Anahtar listesi alınamadı: {loadError}</p>}

      <Dialog open={adding} onOpenChange={setAdding}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Yeni anahtar</DialogTitle>
            <DialogDescription>
              Anahtar yalnız sunucuya gider ve Secret Manager’da “{catalog?.managed_prefix}{slug || 'ad'}” olarak saklanır.
              Sonradan görüntülenemez; aynı adla yeniden kaydetmek yeni sürüm ekler.
            </DialogDescription>
          </DialogHeader>
          <form
            className="space-y-3"
            autoComplete="off"
            onSubmit={(event) => {
              event.preventDefault()
              void store()
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="secret-slug">Ad</Label>
              <Input
                id="secret-slug"
                value={slug}
                onChange={(event) => setSlug(event.target.value.toLowerCase())}
                placeholder="saglayici-anahtari"
              />
              {slug && !SLUG_RE.test(slug) && <p className="text-destructive text-xs">Küçük harf, rakam ve tire kullan.</p>}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="secret-value">Anahtar</Label>
              <Input id="secret-value" type="password" value={secret} onChange={(event) => setSecret(event.target.value)} />
            </div>
            <DialogFooter>
              <Button type="submit" disabled={storing || !SLUG_RE.test(slug) || !secret}>
                Kaydet
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
