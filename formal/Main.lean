import Provenance

open Lean Provenance

def readJsonFile [FromJson α] (path : String) : IO α := do
  let raw ← IO.FS.readFile path
  let parsed ← IO.ofExcept (Json.parse raw)
  IO.ofExcept (fromJson? parsed)

def main (args : List String) : IO UInt32 := do
  match args with
  | [certificatePath, policyPath] =>
    try
      let certificate : Certificate ← readJsonFile certificatePath
      let policy : Policy ← readJsonFile policyPath
      if check policy certificate then
        IO.println "{\"accepted\":true,\"policy\":\"agent-release-v1\"}"
        return 0
      else
        IO.eprintln "provenance-checker: release policy rejected the certificate"
        return 1
    catch _ =>
      IO.eprintln "provenance-checker: invalid certificate or policy JSON"
      return 2
  | _ =>
    IO.eprintln "usage: provenance-checker CERTIFICATE.json POLICY.json"
    return 2
