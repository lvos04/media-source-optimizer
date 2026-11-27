#!/usr/bin/env bash
# De buildengine beheert de proefdata. Ook tussen twee appcontainers en bij
# bind-mounts van submappen moet Docker het volume als in gebruik blijven zien.
start_proefvolume() {
    local naam=$1 image=$2
    proefhouder=$(docker run --detach --name "$naam" --network none \
        --mount type=volume,dst=/proefvolume,volume-nocopy \
        --entrypoint tail "$image" -f /dev/null)
    trap 'docker container rm --force --volumes "$proefhouder" >/dev/null' EXIT
    proefvolume=$(docker container inspect --format \
        '{{range .Mounts}}{{if eq .Destination "/proefvolume"}}{{.Name}}{{end}}{{end}}' "$proefhouder")
    [[ -n "$proefvolume" ]]
}

# De afsluitende jobstap kan ook lopen nadat de EXIT-trap al heeft opgeruimd.
ruim_proefvolume() {
    local namen
    namen=$(docker container ls -a --filter "name=$1" --format '{{.Names}}')
    if grep -Fxq -- "$1" <<<"$namen"; then
        docker container rm --force --volumes "$1" >/dev/null
    fi
}
