# CRUX in a box

This repository is designed to facilitate creating the architecture for [CRUX-style](https://cruxevals.com/) experiments.

## Prerequesties

- A new Mac user account with administrative permissions

## Getting started

1. Run `start.sh`. This will configure a significant portion of the CRUX system automatically.
2. TK set up Telegram (TODO: why tg?)
3. Authenticate any external services your agent will use

## External services for your agent to use

Installed by default:

TODO: make this a table with: service | how it's configured | how to authenticate

- GitHub: `gh` CLI
- AWS: `aws` CLI
<!-- - Gmail: `gog` CLI via [gogcli.sh](https://gogcli.sh/) -->

_NB: none of these are authenticated upon install; you will need to authenticate them separately._
