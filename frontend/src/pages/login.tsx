import { LogoMark } from '@/components/logo'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { PinLogin } from '@/components/wf/pin-login'
import { login } from '@/lib/api'
import { fetchLoginWorkers } from '@/lib/workforce'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

export default function LoginPage() {
  // pracovníci (PIN) sú na mobile prví — majiteľ sa prihlasuje e-mailom
  const workers = useQuery({ queryKey: ['login-workers'], queryFn: fetchLoginWorkers })
  if (workers.isPending) return null
  const hasWorkers = (workers.data?.length ?? 0) > 0

  return (
    <div className="flex min-h-svh items-center justify-center bg-background px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <LogoMark /> rag
          </CardTitle>
          <CardDescription>
            {hasWorkers ? 'Pracovník sa prihlási PIN-om, majiteľ e-mailom.' : 'Prihlás sa do knižnice dokumentov.'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {hasWorkers ? (
            <Tabs defaultValue="pin">
              <TabsList className="mb-4 w-full">
                <TabsTrigger value="pin" className="flex-1">
                  Pracovník
                </TabsTrigger>
                <TabsTrigger value="email" className="flex-1">
                  Majiteľ
                </TabsTrigger>
              </TabsList>
              <TabsContent value="pin">
                <PinLogin />
              </TabsContent>
              <TabsContent value="email">
                <EmailLogin />
              </TabsContent>
            </Tabs>
          ) : (
            <EmailLogin />
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function EmailLogin() {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const submit = useMutation({
    mutationFn: () => login(email, password),
    // reset (nie clear) — aktívne queries sa načítajú znova už so session cookie
    onSuccess: () => void queryClient.resetQueries(),
  })

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault()
        submit.mutate()
      }}
    >
      <div className="space-y-1.5">
        <Label htmlFor="email">E-mail</Label>
        <Input
          id="email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="password">Heslo</Label>
        <Input
          id="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </div>
      {submit.isError && (
        <p className="text-sm text-destructive" role="alert">
          {submit.error.message}
        </p>
      )}
      <Button type="submit" className="w-full" disabled={submit.isPending}>
        Prihlásiť
      </Button>
    </form>
  )
}
