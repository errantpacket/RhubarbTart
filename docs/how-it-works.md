# How it works

Four stages turn a small JSON profile into a proven, named image: Define, Resolve, Build, Prove.

```mermaid
%%{init: {'theme':'base','fontFamily':'ui-sans-serif, system-ui, -apple-system, Helvetica, Arial, sans-serif','themeVariables':{'primaryColor':'#ffffff','primaryTextColor':'#2b2d42','primaryBorderColor':'#c9184a','lineColor':'#8d99ae','edgeLabelBackground':'#ffffff','fontSize':'13px'},'flowchart':{'curve':'basis','nodeSpacing':45,'rankSpacing':55,'padding':8,'useMaxWidth':true}}}%%
flowchart TB
    subgraph D["&nbsp;① Define &nbsp;·&nbsp; profiles, bases, packages (JSON)&nbsp;"]
        direction LR
        PR["profiles/<br/>NAME.json"] ~~~ BA["config/bases/<br/>*.json"] ~~~ PK["config/packages/<br/>*.json"]
    end
    subgraph R["&nbsp;② Resolve &amp; review &nbsp;·&nbsp; the Mac, or any host with gpg&nbsp;"]
        direction LR
        RS["resolve.py<br/>resolve"] --> LK[("locks/<br/>NAME.lock.json")] --> RV{{"you review<br/>&amp; commit"}}
    end
    subgraph B["&nbsp;③ Build &nbsp;·&nbsp; Apple silicon Mac&nbsp;"]
        direction LR
        VF["verify<br/>cache ↔ lock"] --> IN["install from the<br/>vendor IPSW / ISO"] --> GU["guest re-verifies,<br/>hardens, seals"]
    end
    subgraph P["&nbsp;④ Prove &nbsp;·&nbsp; a throwaway clone&nbsp;"]
        direction LR
        UV["…-unverified"] --> ST{{"smoke test<br/>from outside"}}
        ST -->|"pass"| OK["rbt-NAME-sha ✅"]
        ST -->|"fail"| KEEP["kept for<br/>inspection"]
    end
    D ==> R ==> B ==> P

    classDef cfg fill:#fff0f3,stroke:#c9184a,stroke-width:1.5px,color:#2b2d42
    classDef step fill:#ffffff,stroke:#2b2d42,stroke-width:1.5px,color:#2b2d42
    classDef gate fill:#ffd6de,stroke:#c9184a,stroke-width:1.5px,color:#2b2d42
    classDef good fill:#c9184a,stroke:#800f2f,stroke-width:1.5px,color:#ffffff
    classDef bad fill:#2b2d42,stroke:#2b2d42,color:#ffffff
    class PR,BA,PK cfg
    class RS,VF,IN,GU,UV step
    class LK,RV,ST gate
    class OK good
    class KEEP bad
    style D fill:#f7f7f9,stroke:#d9a5b3,color:#6b6b76
    style R fill:#f7f7f9,stroke:#d9a5b3,color:#6b6b76
    style B fill:#f7f7f9,stroke:#d9a5b3,color:#6b6b76
    style P fill:#f7f7f9,stroke:#d9a5b3,color:#6b6b76
```

1. **Define.** A profile names an OS base and a list of tools. Bases and packages declare
   *where* each input comes from and *how* it is verified.
2. **Resolve.** `resolve.py` finds the current versions, downloads them, verifies them, and
   writes a lock. macOS profiles resolve on the Mac; NixOS and Kali resolve anywhere with `gpg`.
   **A human reviews the lock diff before it's committed.**
3. **Build.** `build.sh` re-checks the cache against the lock and installs from the vendor
   image. Inside the guest, every staged file is checked again before install. The guest then
   hardens itself, removes build residue and identity, and powers off.
4. **Prove.** A disposable clone boots and is attacked politely from outside. For a key-SSH image
   (`RHUBARB_SSH_PUBKEYS` set) password login must be refused and key login must work; an
   SSH-disabled image is instead proven to refuse connections on port 22. Either way, no
   auto-login, passwordless sudo or open Screen Sharing is allowed. Only then is the image renamed to `rbt-<profile>-<inputs-sha>`. The name
   is derived from the inputs, so identical inputs give an identical name, and
   `out/<vm>.provenance.json` records exactly what went in.

← back to the [README](../README.md)
